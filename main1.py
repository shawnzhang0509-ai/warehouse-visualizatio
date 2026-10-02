from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import pandas as pd
import os
import uvicorn

# ==========================================
# 1. 初始化
# ==========================================
app = FastAPI(title="Supply Chain Viz - Fixed Chart")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. 数据加载与核心算法
# ==========================================
def load_excel_data():
    excel_path = os.path.join(os.path.dirname(__file__), "advice.xlsx")
    if not os.path.exists(excel_path):
        return pd.DataFrame()
    
    df = pd.read_excel(excel_path)
    df.columns = df.columns.str.strip()
    return df

def find_column(df, keywords):
    for col in df.columns:
        for keyword in keywords:
            if keyword in str(col):
                return col
    return None

def simulate_inventory_flow(stock, transit_qty, transit_days, production_qty, production_days, daily_sales, forecast_days=60):
    """
    模拟真实的库存流动：
    1. 先消耗在库
    2. 在库耗尽后，如果在途到了，接续消耗
    3. 在途耗尽后，如果在产好了，接续消耗
    """
    timeline = []
    current_stock = float(stock) # 确保是浮点数
    
    transit_arrived = False
    production_finished = False
    
    for day in range(1, forecast_days + 1):
        # 1. 检查今天是否有新货入库 (假设第 N 天结束时到货，所以第 N+1 天可用？或者当天可用？
        # 这里假设：物流需 5 天，意味着第 5 天结束时货到，第 6 天开始卖。
        # 为了图表直观，我们让它在第 transit_days 天瞬间增加库存
        
        if not transit_arrived and day == transit_days:
            current_stock += transit_qty
            transit_arrived = True
            
        if not production_finished and day == production_days:
            current_stock += production_qty
            production_finished = True
            
        # 记录当天结束时的库存 (消耗前还是消耗后？通常记录剩余)
        # 这里记录：当天的初始库存 + 到货 - 当天销售
        start_of_day_stock = current_stock
        
        # 消耗
        if daily_sales > 0:
            current_stock -= daily_sales
        
        end_of_day_stock = max(0, current_stock)
        
        timeline.append({
            "day": day,
            "stock_level": end_of_day_stock,
            "is_transit_arrived": transit_arrived,
            "is_production_finished": production_finished
        })
        
        # 优化：如果库存为 0 且没有后续补给，后面全是 0
        if end_of_day_stock <= 0 and transit_arrived and production_finished:
             for remaining in range(day + 1, forecast_days + 1):
                 timeline.append({"day": remaining, "stock_level": 0, "is_transit_arrived": True, "is_production_finished": True})
             break
             
    return timeline

@app.get("/api/all-metrics")
def get_all_metrics():
    try:
        df = load_excel_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if df.empty:
        return {"count": 0, "data": []}

    # --- 列名映射 ---
    c_sku = find_column(df, ['SKU', 'sku', '商品编码'])
    c_region = find_column(df, ['地区', '区域', 'Region'])
    c_stock = find_column(df, ['总数量', '库存', '现有库存', 'Stock', 'OnHand'])
    c_transit = find_column(df, ['在途', '预计入库', 'PO', 'Transit'])
    c_prod = find_column(df, ['在产', '生产中', 'WIP', 'Production'])
    
    # 关键：时间列
    c_transit_time = find_column(df, ['物流时间', '运输天数', 'Lead Time', 'Transit Days'])
    c_prod_time = find_column(df, ['生产时间', '生产周期', 'Production Days', 'Plan Days'])
    
    # 销量
    c_sales = find_column(df, ['最大日销量', '平均日销', '日销', 'Daily Sales'])
    c_decision = find_column(df, ['决策', '建议', 'Decision'])

    if not c_sku:
        return {"count": 0, "data": [], "error": "未找到 SKU 列"}

    results = []
    for _, row in df.iterrows():
        sku = str(row[c_sku]).strip()
        region = str(row[c_region]).strip() if c_region and pd.notna(row[c_region]) else "Unknown"
        decision = str(row[c_decision]).strip() if c_decision and pd.notna(row[c_decision]) else "N/A"
        
        stock = float(row[c_stock]) if c_stock and pd.notna(row[c_stock]) else 0.0
        transit = float(row[c_transit]) if c_transit and pd.notna(row[c_transit]) else 0.0
        production = float(row[c_prod]) if c_prod and pd.notna(row[c_prod]) else 0.0
        sales = float(row[c_sales]) if c_sales and pd.notna(row[c_sales]) else 0.0
        
        t_days = int(row[c_transit_time]) if c_transit_time and pd.notna(row[c_transit_time]) else 0
        p_days = int(row[c_prod_time]) if c_prod_time and pd.notna(row[c_prod_time]) else 0
        
        total_supply = stock + transit + production
        
        # 模拟未来 60 天
        timeline = simulate_inventory_flow(stock, transit, t_days, production, p_days, sales, forecast_days=60)
        
        # 风险判定升级
        risk = "Low"
        if sales > 0:
            days_of_stock = stock / sales
            # 如果在库卖完的时间 < 物流时间，说明中间会断货
            if days_of_stock < t_days: 
                risk = "High" 
            elif days_of_stock < t_days + (transit/sales if sales > 0 else 999):
                risk = "Medium" 
            else:
                risk = "Low"
        else:
            if total_supply == 0: risk = "Unknown"
            else: risk = "Safe"

        results.append({
            "sku": sku,
            "region": region,
            "decision": decision,
            "stock": round(stock, 1),
            "transit": round(transit, 1),
            "production": round(production, 1),
            "sales": round(sales, 2),
            "transit_days": t_days,
            "prod_days": p_days,
            "total_days": round(total_supply / sales, 1) if sales > 0 else 999,
            "risk": risk,
            "timeline": timeline 
        })
    
    return {"count": len(results), "data": results}

@app.get("/api/all-skus")
def get_all_skus_list():
    try:
        df = load_excel_data()
        c_sku = find_column(df, ['SKU', 'sku', '商品编码'])
        if not c_sku: return {"skus": []}
        return {"skus": [str(x) for x in df[c_sku].unique().tolist()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 3. 前端页面 (修复图表部分)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def dashboard_home():
    # 首页代码保持不变，略... (为了节省篇幅，直接复用之前的逻辑，主要修复列表页)
    # 实际使用时请保留之前的首页代码，或者这里简单返回一个跳转
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>单品深度分析</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 20px; text-align: center; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
            h1 { color: #2c3e50; }
            a { color: #3498db; text-decoration: none; font-size: 18px; font-weight: bold; }
            .btn { display: inline-block; margin-top: 20px; padding: 10px 20px; background: #3498db; color: white; border-radius: 5px; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📦 供应链可视化系统</h1>
            <p>请选择功能模块：</p>
            <a href="/all-skus-view" class="btn">👉 去全量监控列表 (带图表)</a>
            <br><br>
            <small>注：单品分析页功能已整合进列表页点击详情中</small>
        </div>
    </body>
    </html>
    """

@app.get("/all-skus-view", response_class=HTMLResponse)
def all_skus_view():
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>全量 SKU 监控与预测</title>
        <!-- 引入 Chart.js -->
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; padding: 20px; }
            .container { max-width: 1400px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #2c3e50; display: flex; justify-content: space-between; align-items: center; }
            .nav-link a { color: #3498db; text-decoration: none; font-weight: bold; font-size: 14px; }
            
            .controls { display: flex; gap: 10px; margin-bottom: 20px; }
            .search-box { padding: 10px; width: 300px; border: 1px solid #ddd; border-radius: 4px; }
            
            table { width: 100%; border-collapse: collapse; font-size: 14px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
            th { background-color: #f8f9fa; color: #2c3e50; cursor: pointer; user-select: none; }
            th:hover { background-color: #e9ecef; }
            th::after { content: ' ↕'; font-size: 10px; color: #aaa; }
            
            tr:hover { background-color: #f1f1f1; cursor: pointer; }
            
            .badge { padding: 4px 8px; border-radius: 4px; font-size: 12px; color: white; font-weight: bold; }
            .bg-high { background-color: #e74c3c; }
            .bg-medium { background-color: #f39c12; }
            .bg-low { background-color: #27ae60; }
            .bg-safe { background-color: #95a5a6; }

            /* 弹窗样式 */
            .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(0,0,0,0.5); }
            .modal-content { background-color: #fefefe; margin: 5% auto; padding: 20px; border: 1px solid #888; width: 80%; max-width: 900px; border-radius: 8px; }
            .close { color: #aaa; float: right; font-size: 28px; font-weight: bold; cursor: pointer; }
            .close:hover { color: black; }
            .chart-container { position: relative; height: 400px; width: 100%; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>
                📊 全量 SKU 库存表现监控
                <span class="nav-link"><a href="/">👈 返回首页</a></span>
            </h1>
            
            <div class="controls">
                <input type="text" id="searchInput" class="search-box" placeholder="🔍 搜索 SKU 或 地区..." onkeyup="filterTable()">
            </div>

            <div id="loading" style="text-align:center; padding:20px;">正在加载数据...</div>
            
            <table id="metricsTable" style="display:none;">
                <thead>
                    <tr>
                        <th onclick="sortTable('sku')">SKU</th>
                        <th onclick="sortTable('region')">地区</th>
                        <th onclick="sortTable('sales', true)" style="background:#e8f6f3;">🔥 日销量 ↕</th>
                        <th onclick="sortTable('stock', true)">在库数量 ↕</th>
                        <th onclick="sortTable('transit', true)">在途数量 ↕</th>
                        <th onclick="sortTable('production', true)">在产数量 ↕</th>
                        <th onclick="sortTable('total_days', true)" style="background:#fff3cd;">总可售天数 ↕</th>
                        <th onclick="sortTable('risk')">风险等级</th>
                        <th>建议决策</th>
                    </tr>
                </thead>
                <tbody id="tableBody"></tbody>
            </table>
        </div>

        <!-- 详情弹窗 -->
        <div id="detailModal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="closeModal()">&times;</span>
                <h2 id="modalTitle">SKU 库存走势预测</h2>
                <p id="modalDesc" style="color:#666; font-size: 14px;"></p>
                <div class="chart-container">
                    <canvas id="trendChart"></canvas>
                </div>
                <div style="margin-top:10px; font-size:12px; color:#888;">
                    💡 提示：曲线下降代表销售消耗，曲线突然上升代表货物到达（在途或在产）。如果曲线触底（0）且长时间不回升，即为断货。
                </div>
            </div>
        </div>

        <script>
            let allData = [];
            let currentChart = null;

            async function loadData() {
                try {
                    const res = await fetch('/api/all-metrics');
                    const result = await res.json();
                    allData = result.data;
                    // 默认按总天数升序排列（最危险的在前面）
                    allData.sort((a, b) => a.total_days - b.total_days);
                    renderTable(allData);
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('metricsTable').style.display = 'table';
                } catch (e) { 
                    document.getElementById('loading').innerText = "加载失败: " + e.message; 
                }
            }

            function renderTable(data) {
                const tbody = document.getElementById('tableBody');
                tbody.innerHTML = '';
                data.forEach(item => {
                    let badgeClass = 'bg-safe';
                    if (item.risk === 'High') badgeClass = 'bg-high';
                    else if (item.risk === 'Medium') badgeClass = 'bg-medium';
                    else if (item.risk === 'Low') badgeClass = 'bg-low';
                    
                    const tr = document.createElement('tr');
                    tr.onclick = () => showDetail(item);
                    
                    tr.innerHTML = `
                        <td><strong>${item.sku}</strong></td>
                        <td>${item.region}</td>
                        <td style="font-weight:bold; color:#16a085">${item.sales}</td>
                        <td>${item.stock}</td>
                        <td>${item.transit} <span style="font-size:10px;color:#999">(${item.transit_days}天)</span></td>
                        <td>${item.production} <span style="font-size:10px;color:#999">(${item.prod_days}天)</span></td>
                        <td style="font-weight:bold; color:${item.total_days < 7 ? '#e74c3c' : '#2c3e50'}">${item.total_days}</td>
                        <td><span class="badge ${badgeClass}">${item.risk}</span></td>
                        <td>${item.decision}</td>
                    `;
                    tbody.appendChild(tr);
                });
            }

            function filterTable() {
                const query = document.getElementById('searchInput').value.toLowerCase();
                const filtered = allData.filter(item => 
                    item.sku.toLowerCase().includes(query) || 
                    item.region.toLowerCase().includes(query)
                );
                renderTable(filtered);
            }

            let sortAsc = true;
            function sortTable(key, isNumber = false) {
                sortAsc = !sortAsc;
                allData.sort((a, b) => {
                    let valA = a[key];
                    let valB = b[key];
                    if (isNumber) {
                        return sortAsc ? valA - valB : valB - valA;
                    } else {
                        return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
                    }
                });
                renderTable(allData);
            }

            function showDetail(item) {
                document.getElementById('detailModal').style.display = "block";
                document.getElementById('modalTitle').innerText = `${item.sku} (${item.region}) - 库存消耗模拟`;
                document.getElementById('modalDesc').innerText = `当前日销：${item.sales} | 物流需 ${item.transit_days} 天 | 生产需 ${item.prod_days} 天 | 建议：${item.decision}`;
                
                // 稍微延迟一下确保 DOM 渲染完成
                setTimeout(() => {
                    drawTrendChart(item);
                }, 50);
            }

            function closeModal() {
                document.getElementById('detailModal').style.display = "none";
                if (currentChart) {
                    currentChart.destroy();
                    currentChart = null;
                }
            }
            
            window.onclick = function(event) {
                if (event.target == document.getElementById('detailModal')) {
                    closeModal();
                }
            }

            function drawTrendChart(item) {
                const ctx = document.getElementById('trendChart').getContext('2d');
                if (currentChart) currentChart.destroy();

                if (!item.timeline || item.timeline.length === 0) {
                    alert("暂无模拟数据");
                    return;
                }

                const labels = item.timeline.map(t => `第${t.day}天`);
                const dataPoints = item.timeline.map(t => t.stock_level);
                
                // 找出关键点用于 Tooltip 提示
                const transitDay = item.timeline.find(t => t.is_transit_arrived)?.day || null;
                const prodDay = item.timeline.find(t => t.is_production_finished)?.day || null;

                currentChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: '实时库存水位',
                            data: dataPoints,
                            borderColor: '#3498db',
                            backgroundColor: 'rgba(52, 152, 219, 0.2)',
                            fill: true,
                            tension: 0.1, 
                            pointRadius: 2,
                            pointHoverRadius: 5
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {
                            mode: 'index',
                            intersect: false,
                        },
                        plugins: {
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        let label = context.dataset.label || '';
                                        if (label) {
                                            label += ': ';
                                        }
                                        if (context.parsed.y !== null) {
                                            label += context.parsed.y.toFixed(1);
                                        }
                                        
                                        // 自定义 Tooltip 内容，显示是否到货
                                        const dayIndex = context.dataIndex;
                                        const dayData = item.timeline[dayIndex];
                                        let extra = "";
                                        if (dayData.is_transit_arrived && !item.timeline[dayIndex-1]?.is_transit_arrived) {
                                            extra += " \\n🚚 在途货物今日到达!";
                                        }
                                        if (dayData.is_production_finished && !item.timeline[dayIndex-1]?.is_production_finished) {
                                            extra += " \\n🏭 生产货物今日完工!";
                                        }
                                        return label + extra;
                                    }
                                }
                            },
                            legend: { display: true }
                        },
                        scales: {
                            y: { 
                                beginAtZero: true, 
                                title: { display: true, text: '剩余库存数量' },
                                grid: { color: '#f0f0f0' }
                            },
                            x: { 
                                title: { display: true, text: '未来天数' },
                                grid: { display: false }
                            }
                        }
                    }
                });
            }

            loadData();
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    print("🚀 服务启动成功！")
    print("👉 访问地址：http://localhost:8000/all-skus-view")
    uvicorn.run(app, host="0.0.0.0", port=8000)