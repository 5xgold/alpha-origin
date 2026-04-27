"""
可视化模块：生成自包含 HTML 报告
包含：
  - 当前股票 K 线 + 技术指标（ECharts）
  - 相似历史案例价格走势对比图
  - 后验收益率分布直方图
  - 综合评分雷达图
  - 分年度胜率热力图
  - 交易建议摘要卡片
"""

import json
import os
from typing import List, Dict, Optional
import pandas as pd
import numpy as np

from similarity.retrieval import SearchResult
from backtest.analyzer import BacktestStats, compute_score


def _to_js(obj) -> str:
    """安全序列化为 JS 值"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def generate_html_report(
    query_stock: str,
    query_df: pd.DataFrame,           # 当前股票完整特征 DataFrame
    results: List[SearchResult],
    stats: BacktestStats,
    output_path: str = "output/report.html",
) -> str:
    """
    生成完整的 HTML 分析报告（自包含 CSS/JS）
    返回文件路径
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    scores    = compute_score(stats)
    top5      = results[:5]

    # ── 准备图表数据 ─────────────────────────────────────────────
    # K 线数据（最近 60 个交易日）
    kline_df  = query_df.tail(60).copy()
    dates     = [str(d)[:10] for d in kline_df.index]

    kline_data = [
        [row["open"], row["close"], row["low"], row["high"]]
        for _, row in kline_df.iterrows()
    ]
    volume_data = kline_df["volume"].tolist()
    ma5_data    = kline_df["ma5"].round(2).tolist()
    ma20_data   = kline_df["ma20"].round(2).tolist()
    macd_hist   = kline_df["macd_hist"].round(4).tolist()
    rsi_data    = kline_df["rsi"].round(2).tolist()

    # 相似案例归一化价格（只取前 5）
    similar_lines = []
    for r in top5:
        p = r.sample.price_norm.tolist()
        similar_lines.append({
            "name": f"{r.sample.stock_code} {r.sample.end_date}",
            "data": [round(x, 4) for x in p],
            "score": round(r.combined_score, 3),
            "ret":   f"{r.sample.future_return:+.1%}",
        })

    # 归一化当前价格序列
    cur_price_norm = (
        kline_df["close"] / kline_df["close"].iloc[0] - 1
    ).round(4).tolist()

    # 收益率分布
    ret_hist_bins = np.linspace(-0.2, 0.4, 25)
    ret_hist_vals, bin_edges = np.histogram(stats.returns, bins=ret_hist_bins)
    hist_labels = [f"{x:.0%}" for x in bin_edges[:-1]]

    # 雷达图数据
    radar_vals = [
        scores["胜率得分"],
        scores["盈亏比得分"],
        scores["收益得分"],
        scores["回撤控制"],
    ]

    # 分年度胜率（用于热力图）
    year_stats: Dict[str, dict] = {}
    for r in results:
        y = r.sample.end_date[:4]
        if y not in year_stats:
            year_stats[y] = {"wins": 0, "total": 0}
        year_stats[y]["total"] += 1
        if r.sample.future_return >= 0.10:
            year_stats[y]["wins"] += 1
    year_labels = sorted(year_stats.keys())
    year_winrates = [
        round(year_stats[y]["wins"] / year_stats[y]["total"] * 100, 1)
        for y in year_labels
    ]

    # ── HTML 模板 ────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>形态相似检索报告 — {query_stock}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
          background: #0f1117; color: #e2e8f0; min-height: 100vh; }}
  .header {{ background: linear-gradient(135deg,#1a1f2e,#16213e);
             padding: 24px 32px; border-bottom: 1px solid #2d3748; }}
  .header h1 {{ font-size: 22px; color: #e2e8f0; }}
  .header h1 span {{ color: #f6ad55; }}
  .header p {{ color: #718096; font-size: 13px; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4,1fr);
           gap: 16px; padding: 20px 32px; }}
  .card {{ background: #1a1f2e; border-radius: 10px;
           border: 1px solid #2d3748; padding: 20px; }}
  .card.wide2 {{ grid-column: span 2; }}
  .card.wide4 {{ grid-column: span 4; }}
  .kv-label {{ font-size: 12px; color: #718096; margin-bottom: 4px; }}
  .kv-value {{ font-size: 24px; font-weight: 700; }}
  .kv-value.up {{ color: #fc8181; }}
  .kv-value.neutral {{ color: #f6ad55; }}
  .kv-value.down {{ color: #68d391; }}
  .section-title {{ font-size: 14px; color: #a0aec0; font-weight: 600;
                    margin-bottom: 12px; }}
  .chart {{ width: 100%; height: 320px; }}
  .chart-tall {{ width: 100%; height: 400px; }}
  .similar-list {{ font-size: 13px; }}
  .similar-item {{ display: flex; align-items: center;
                   padding: 10px 0; border-bottom: 1px solid #2d3748; }}
  .sim-badge {{ background: #2d3748; border-radius: 4px;
                padding: 2px 8px; font-size: 11px;
                margin-right: 10px; min-width: 48px; text-align:center; }}
  .sim-meta {{ flex: 1; color: #cbd5e0; }}
  .sim-ret {{ font-weight: 700; min-width: 60px; text-align:right; }}
  .sim-ret.up {{ color: #fc8181; }}
  .sim-ret.down {{ color: #68d391; }}
  .warn-box {{ background:#2d3748; border-left: 3px solid #f6ad55;
               padding:12px 16px; border-radius:0 6px 6px 0;
               font-size:12px; color:#a0aec0; line-height:1.8; }}
</style>
</head>
<body>

<div class="header">
  <h1>形态相似检索报告 — <span>{query_stock}</span></h1>
  <p>基于历史成功案例的走势相似性分析 | 仅作研究参考，不构成投资建议</p>
</div>

<div class="grid">

  <!-- KV 卡片 -->
  <div class="card">
    <div class="kv-label">相似案例总数</div>
    <div class="kv-value neutral">{stats.total_cases}</div>
  </div>
  <div class="card">
    <div class="kv-label">历史胜率（≥10%）</div>
    <div class="kv-value {'up' if stats.win_rate>=0.5 else 'neutral'}">{stats.win_rate:.1%}</div>
  </div>
  <div class="card">
    <div class="kv-label">均值后验收益</div>
    <div class="kv-value {'up' if stats.mean_return>0 else 'down'}">{stats.mean_return:+.1%}</div>
  </div>
  <div class="card">
    <div class="kv-label">综合评分</div>
    <div class="kv-value neutral">{scores['总分']:.0f} / 100</div>
  </div>

  <!-- K 线图 -->
  <div class="card wide4">
    <div class="section-title">当前股票 K 线（近 60 个交易日）</div>
    <div id="kline" class="chart-tall"></div>
  </div>

  <!-- 技术指标 MACD + RSI -->
  <div class="card wide2">
    <div class="section-title">MACD 柱状图</div>
    <div id="macd" class="chart"></div>
  </div>
  <div class="card wide2">
    <div class="section-title">RSI（14）</div>
    <div id="rsi" class="chart"></div>
  </div>

  <!-- 相似案例走势对比 -->
  <div class="card wide2">
    <div class="section-title">相似历史案例归一化走势对比（TOP 5）</div>
    <div id="similarLines" class="chart"></div>
  </div>

  <!-- 相似案例列表 -->
  <div class="card wide2">
    <div class="section-title">TOP 5 最相似案例</div>
    <div class="similar-list">
      {"".join([
        f'''<div class="similar-item">
          <div class="sim-badge">#{i+1} {r.combined_score:.3f}</div>
          <div class="sim-meta">{r.sample.stock_code}<br>
            <span style="color:#718096;font-size:11px">{r.sample.end_date}</span>
          </div>
          <div class="sim-ret {'up' if r.sample.future_return>0 else 'down'}">
            {r.sample.future_return:+.1%}
          </div>
        </div>'''
        for i, r in enumerate(top5)
      ])}
    </div>
  </div>

  <!-- 收益率分布直方图 -->
  <div class="card wide2">
    <div class="section-title">后验收益率分布（{stats.total_cases} 个样本）</div>
    <div id="retHist" class="chart"></div>
  </div>

  <!-- 雷达图 -->
  <div class="card wide2">
    <div class="section-title">综合评分雷达图</div>
    <div id="radar" class="chart"></div>
  </div>

  <!-- 分年度胜率 -->
  <div class="card wide2">
    <div class="section-title">历年胜率变化</div>
    <div id="yearBar" class="chart"></div>
  </div>

  <!-- 关键统计 -->
  <div class="card wide2">
    <div class="section-title">风险收益统计</div>
    <table style="width:100%;font-size:13px;border-collapse:collapse;">
      <tr style="border-bottom:1px solid #2d3748">
        <td style="padding:8px;color:#718096">最大收益</td>
        <td style="padding:8px;color:#fc8181;font-weight:700">{stats.max_return:+.1%}</td>
        <td style="padding:8px;color:#718096">最大亏损</td>
        <td style="padding:8px;color:#68d391;font-weight:700">{stats.min_return:+.1%}</td>
      </tr>
      <tr style="border-bottom:1px solid #2d3748">
        <td style="padding:8px;color:#718096">中位收益</td>
        <td style="padding:8px;color:#e2e8f0">{stats.median_return:+.1%}</td>
        <td style="padding:8px;color:#718096">收益标准差</td>
        <td style="padding:8px;color:#e2e8f0">{stats.std_return:.1%}</td>
      </tr>
      <tr style="border-bottom:1px solid #2d3748">
        <td style="padding:8px;color:#718096">盈亏比</td>
        <td style="padding:8px;color:#f6ad55;font-weight:700">{stats.profit_loss_ratio:.2f}x</td>
        <td style="padding:8px;color:#718096">25/75 分位</td>
        <td style="padding:8px;color:#e2e8f0">{stats.pct_25:+.1%} / {stats.pct_75:+.1%}</td>
      </tr>
      <tr>
        <td style="padding:8px;color:#718096">均值回撤</td>
        <td style="padding:8px;color:#fc8181">{stats.mean_drawdown:.1%}</td>
        <td style="padding:8px;color:#718096">最大回撤</td>
        <td style="padding:8px;color:#fc8181">{stats.max_drawdown:.1%}</td>
      </tr>
    </table>
  </div>

  <!-- 风险提示 -->
  <div class="card wide4">
    <div class="warn-box">
      ⚠️ <strong>免责声明</strong>：本报告基于历史走势相似性统计，不构成任何投资建议。
      股市有风险，过去表现不代表未来收益。请结合基本面、宏观环境、风险承受能力做综合判断。
      历史胜率统计可能受到幸存者偏差、市场环境变化等因素影响，请谨慎使用。
    </div>
  </div>

</div>

<script>
// ── 数据注入 ──────────────────────────────────────────────────────
const dates       = {_to_js(dates)};
const klineData   = {_to_js(kline_data)};
const volumeData  = {_to_js(volume_data)};
const ma5         = {_to_js(ma5_data)};
const ma20        = {_to_js(ma20_data)};
const macdHist    = {_to_js(macd_hist)};
const rsiData     = {_to_js(rsi_data)};
const simLines    = {_to_js(similar_lines)};
const curNorm     = {_to_js(cur_price_norm)};
const histLabels  = {_to_js(hist_labels)};
const histVals    = {_to_js(ret_hist_vals.tolist())};
const radarVals   = {_to_js(radar_vals)};
const yearLabels  = {_to_js(year_labels)};
const yearWinrates= {_to_js(year_winrates)};

// ── K 线图 ────────────────────────────────────────────────────────
const klineChart = echarts.init(document.getElementById('kline'));
klineChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
  legend: {{ data: ['K线','MA5','MA20'], textStyle: {{ color:'#a0aec0' }} }},
  grid: [{{ left:'5%', right:'3%', top:'8%', height:'60%' }},
         {{ left:'5%', right:'3%', bottom:'3%', height:'20%' }}],
  xAxis: [
    {{ type:'category', data:dates, gridIndex:0,
       axisLabel:{{ color:'#718096', fontSize:10 }}, axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
    {{ type:'category', data:dates, gridIndex:1,
       axisLabel:{{ color:'#718096', fontSize:10 }}, axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  ],
  yAxis: [
    {{ scale:true, splitLine:{{lineStyle:{{color:'#1e2535'}}}},
       axisLabel:{{ color:'#718096', fontSize:10 }} }},
    {{ scale:true, gridIndex:1, splitNumber:2,
       splitLine:{{lineStyle:{{color:'#1e2535'}}}},
       axisLabel:{{ color:'#718096', fontSize:10 }} }},
  ],
  dataZoom: [{{ type:'inside', xAxisIndex:[0,1], start:0, end:100 }}],
  series: [
    {{ name:'K线', type:'candlestick', data:klineData,
       itemStyle:{{ color:'#fc8181', color0:'#68d391',
                    borderColor:'#fc8181', borderColor0:'#68d391' }} }},
    {{ name:'MA5', type:'line', data:ma5, smooth:true,
       lineStyle:{{color:'#f6ad55',width:1}}, symbol:'none' }},
    {{ name:'MA20', type:'line', data:ma20, smooth:true,
       lineStyle:{{color:'#76e4f7',width:1}}, symbol:'none' }},
    {{ name:'成交量', type:'bar', data:volumeData, xAxisIndex:1, yAxisIndex:1,
       itemStyle:{{ color:(p)=>klineData[p.dataIndex][1]>=klineData[p.dataIndex][0]
                               ?'#fc8181':'#68d391' }} }},
  ]
}});

// ── MACD ────────────────────────────────────────────────────────
const macdChart = echarts.init(document.getElementById('macd'));
macdChart.setOption({{
  backgroundColor:'transparent',
  grid: {{ left:'8%', right:'3%', top:'10%', bottom:'8%' }},
  xAxis: {{ type:'category', data:dates,
            axisLabel:{{ color:'#718096',fontSize:9 }},
            axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  yAxis: {{ scale:true, splitLine:{{lineStyle:{{color:'#1e2535'}}}},
            axisLabel:{{ color:'#718096',fontSize:9 }} }},
  tooltip: {{ trigger:'axis' }},
  dataZoom: [{{ type:'inside', start:0, end:100 }}],
  series: [{{
    type:'bar', data:macdHist,
    itemStyle:{{ color:(p)=>macdHist[p.dataIndex]>=0?'#fc8181':'#68d391' }}
  }}]
}});

// ── RSI ─────────────────────────────────────────────────────────
const rsiChart = echarts.init(document.getElementById('rsi'));
rsiChart.setOption({{
  backgroundColor:'transparent',
  grid: {{ left:'8%', right:'3%', top:'10%', bottom:'8%' }},
  xAxis: {{ type:'category', data:dates,
            axisLabel:{{ color:'#718096',fontSize:9 }},
            axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  yAxis: {{ min:0, max:100, splitLine:{{lineStyle:{{color:'#1e2535'}}}},
            axisLabel:{{ color:'#718096',fontSize:9 }} }},
  tooltip: {{ trigger:'axis' }},
  markLine: {{ data:[{{ yAxis:70,lineStyle:{{color:'#fc8181',type:'dashed'}} }},
                     {{ yAxis:30,lineStyle:{{color:'#68d391',type:'dashed'}} }}] }},
  dataZoom: [{{ type:'inside', start:0, end:100 }}],
  series: [{{
    type:'line', data:rsiData, smooth:true,
    lineStyle:{{ color:'#b794f4',width:1.5 }}, symbol:'none',
    areaStyle:{{ color:'rgba(183,148,244,0.08)' }},
    markLine:{{
      data:[
        {{ yAxis:70, lineStyle:{{color:'#fc8181',type:'dashed',width:1}} }},
        {{ yAxis:30, lineStyle:{{color:'#68d391',type:'dashed',width:1}} }},
      ],
      label:{{ color:'#a0aec0', fontSize:10 }}
    }}
  }}]
}});

// ── 相似走势对比 ─────────────────────────────────────────────────
const simChart = echarts.init(document.getElementById('similarLines'));
const simSeries = simLines.map((s,i)=>{{
  const colors = ['#fc8181','#f6ad55','#68d391','#76e4f7','#b794f4'];
  return {{
    name: s.name, type:'line', data:s.data, smooth:true, symbol:'none',
    lineStyle:{{ color:colors[i%5], width:1.2, opacity:0.7, type:'dashed' }},
  }};
}});
simSeries.unshift({{
  name:'当前', type:'line', data:curNorm, smooth:true, symbol:'none',
  lineStyle:{{ color:'#ffffff', width:2 }},
  z:10
}});
simChart.setOption({{
  backgroundColor:'transparent',
  legend:{{ orient:'vertical', right:0, top:10,
            textStyle:{{color:'#a0aec0',fontSize:10}} }},
  grid:{{ left:'8%', right:'28%', top:'5%', bottom:'8%' }},
  xAxis:{{ type:'category',
           data:Array.from({{length:simLines.length?simLines[0].data.length:60}},(_,i)=>i+1),
           axisLabel:{{ color:'#718096',fontSize:9 }},
           axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  yAxis:{{ scale:true, splitLine:{{lineStyle:{{color:'#1e2535'}}}},
           axisLabel:{{ color:'#718096',fontSize:9,
                        formatter:v=>(v*100).toFixed(1)+'%' }} }},
  tooltip:{{ trigger:'axis', formatter:p=>p.map(s=>`${{s.seriesName}}: ${{(s.value*100).toFixed(2)}}%`).join('<br>') }},
  series:simSeries
}});

// ── 收益率分布直方图 ─────────────────────────────────────────────
const histChart = echarts.init(document.getElementById('retHist'));
histChart.setOption({{
  backgroundColor:'transparent',
  grid:{{ left:'8%', right:'3%', top:'10%', bottom:'15%' }},
  xAxis:{{ type:'category', data:histLabels,
           axisLabel:{{ color:'#718096',fontSize:9,rotate:30 }},
           axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  yAxis:{{ splitLine:{{lineStyle:{{color:'#1e2535'}}}},
           axisLabel:{{ color:'#718096',fontSize:9 }} }},
  tooltip:{{ trigger:'axis' }},
  series:[{{
    type:'bar', data:histVals,
    itemStyle:{{ color:(p)=>{{
      const label = histLabels[p.dataIndex];
      const val = parseFloat(label);
      return val >= 0.10 ? '#fc8181' : val >= 0 ? '#f6ad55' : '#68d391';
    }} }}
  }}]
}});

// ── 雷达图 ─────────────────────────────────────────────────────
const radarChart = echarts.init(document.getElementById('radar'));
radarChart.setOption({{
  backgroundColor:'transparent',
  radar:{{
    indicator:[
      {{name:'胜率',max:40}}, {{name:'盈亏比',max:20}},
      {{name:'收益',max:20}}, {{name:'回撤控制',max:20}},
    ],
    axisLine:{{lineStyle:{{color:'#2d3748'}}}},
    splitLine:{{lineStyle:{{color:'#2d3748'}}}},
    name:{{textStyle:{{color:'#a0aec0',fontSize:12}}}},
  }},
  series:[{{
    type:'radar',
    data:[{{ value:radarVals, name:'当前形态',
             lineStyle:{{color:'#f6ad55',width:2}},
             areaStyle:{{color:'rgba(246,173,85,0.15)'}},
             itemStyle:{{color:'#f6ad55'}} }}],
  }}],
  legend:{{ show:false }},
}});

// ── 分年度胜率 ───────────────────────────────────────────────────
const yearChart = echarts.init(document.getElementById('yearBar'));
yearChart.setOption({{
  backgroundColor:'transparent',
  grid:{{ left:'8%', right:'3%', top:'10%', bottom:'8%' }},
  xAxis:{{ type:'category', data:yearLabels,
           axisLabel:{{ color:'#718096',fontSize:11 }},
           axisLine:{{lineStyle:{{color:'#2d3748'}}}} }},
  yAxis:{{ max:100, splitLine:{{lineStyle:{{color:'#1e2535'}}}},
           axisLabel:{{ color:'#718096',fontSize:9,formatter:v=>v+'%' }} }},
  tooltip:{{ formatter:p=>`${{p.name}} 年：胜率 ${{p.value}}%` }},
  series:[{{
    type:'bar', data:yearWinrates,
    itemStyle:{{ color:(p)=>p.value>=50?'#fc8181':'#68d391',
                 borderRadius:[3,3,0,0] }},
    label:{{ show:true, position:'top', color:'#a0aec0', fontSize:11,
             formatter:p=>p.value+'%' }}
  }}],
  markLine:{{ data:[{{ yAxis:50, lineStyle:{{color:'#f6ad55',type:'dashed'}} }}] }}
}});

// ── 响应式 ──────────────────────────────────────────────────────
window.addEventListener('resize', ()=>
  [klineChart,macdChart,rsiChart,simChart,histChart,radarChart,yearChart]
  .forEach(c=>c.resize())
);
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
