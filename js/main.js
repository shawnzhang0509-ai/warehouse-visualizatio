// js/main.js

// 1. 定义仓库数据 (硬编码，防止 CSV 加载失败)
const warehouseData = [
    { name: 'Walls Road', value: 15000, capacity: 20000, coords: [174.7633, -36.8950] },
    { name: 'Carbine Rd', value: 8500, capacity: 12000, coords: [174.7800, -36.8800] },
    { name: 'CHCH Gerald', value: 6000, capacity: 10000, coords: [172.6360, -43.5320] },
    { name: 'Presale-AKL', value: 2000, capacity: 5000, coords: [174.7600, -36.8400] },
    { name: 'Presale-CHCH', value: 1200, capacity: 3000, coords: [172.6300, -43.5100] }
];

document.addEventListener('DOMContentLoaded', function () {
    const chartDom = document.getElementById('warehouse-map');
    if (!chartDom) {
        console.error('找不到 id 为 "warehouse-map" 的 div');
        return;
    }
    
    const myChart = echarts.init(chartDom);
    
    // 显示加载提示
    chartDom.innerHTML = '<div style="text-align:center;padding-top:50px;color:#666;">地图加载中...</div>';

    // 2. 加载地图
    // 注意：如果你的 nz.json 在 geo 文件夹里，请改成 'geo/nz.json'
    // 如果在根目录，就是 'nz.json'
    fetch('nz.json') 
        .then(response => {
            if (!response.ok) throw new Error('找不到地图文件 nz.json (404)');
            return response.json();
        })
        .then(mapData => {
            // 注册地图
            echarts.registerMap('NZ', mapData);
            
            // 准备数据
            const seriesData = warehouseData.map(item => ({
                name: item.name,
                value: [...item.coords, item.value]
            }));

            // 3. 渲染配置
            myChart.setOption({
                backgroundColor: '#f4f4f4', // 背景色
                title: {
                    text: '新西兰仓库分布图',
                    left: 'center',
                    top: 20
                },
                tooltip: { trigger: 'item' },
                geo: {
                    map: 'NZ',
                    roam: true, // 允许缩放
                    center: [173.5, -41.0], // 强制设置中心点 (新西兰中间)
                    zoom: 4, // 强制设置缩放级别
                    itemStyle: { 
                        areaColor: '#e0e0e0', 
                        borderColor: '#999',
                        shadowColor: 'rgba(0,0,0,0.2)',
                        shadowBlur: 10
                    }
                },
                series: [
                    {
                        name: '仓库',
                        type: 'scatter',
                        coordinateSystem: 'geo',
                        data: seriesData,
                        symbolSize: function (val) {
                            return Math.sqrt(val) / 2; // 根据数值大小调整点的大小
                        },
                        itemStyle: { color: '#d73027' }, // 点的颜色
                        label: {
                            show: true,
                            formatter: '{b}',
                            position: 'right',
                            color: '#333'
                        }
                    }
                ]
            });
        })
        .catch(err => {
            console.error(err);
            chartDom.innerHTML = `<h3 style="color:red; text-align:center; padding-top:50px;">
                出错了：<br/>${err.message}<br/>
                请检查 nz.json 是否存在且路径正确<websource>source_group_web_1</websource>。
            </h3>`;
        });
});