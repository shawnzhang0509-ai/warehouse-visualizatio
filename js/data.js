// 1. 配置你的数据和参数
// 仓库数据配置 (这里只保留名称和数值，坐标单独放)
const warehouseDataConfig = [
    { name: 'Walls Road', value: 15000, capacity: 20000 },
    { name: 'Carbine Rd Warehouse', value: 8500, capacity: 12000 },
    { name: 'CHCH Gerald Connelly', value: 6000, capacity: 10000 },
    { name: 'Presale-AKL', value: 2000, capacity: 5000 },
    { name: 'Presale-CHCH', value: 1200, capacity: 3000 }
];

// 仓库经纬度坐标 (经度, 纬度)
const geoCoordMap = {
    'Walls Road': [174.7633, -36.8950], // 奥克兰
    'Carbine Rd Warehouse': [174.7800, -36.8800], // 奥克兰
    'CHCH Gerald Connelly': [172.6360, -43.5320], // 基督城
    'Presale-AKL': [174.7600, -36.8400], // 奥克兰
    'Presale-CHCH': [172.6300, -43.5100] // 基督城
};

// 地图配置
const mapConfig = {
    mapUrl: 'https://raw.githubusercontent.com/apache/echarts-examples/gh-pages/public/data/asset/geo/NZ.json',
    zoom: 4,
    center: [173.5, -41.5] // 新西兰中心坐标
};

// 颜色配置
const colorConfig = {
    normal: '#1890ff',
    warning: '#faad14',
    danger: '#ff4d4f'
};

// 2. 页面加载完成后执行
document.addEventListener('DOMContentLoaded', async function () {
    const chartDom = document.getElementById('warehouse-map');
    const myChart = echarts.init(chartDom);

    try {
        // --- 关键步骤：加载地图底图数据 ---
        // 这一步会去下载你提供的 NZ.json 文件
        const mapJson = await fetch(mapConfig.mapUrl).then(res => res.json());
        echarts.registerMap('New Zealand', mapJson);

        // --- 准备图表数据 ---
        // 将配置数据转换成 ECharts 需要的格式: [经度, 纬度, 体积]
        const seriesData = warehouseDataConfig.map(item => {
            const coords = geoCoordMap[item.name];
            // 计算占用率用于颜色判断
            const ratio = item.value / item.capacity;
            let itemColor = colorConfig.normal;
            if (ratio > 0.8) itemColor = colorConfig.danger;
            else if (ratio > 0.6) itemColor = colorConfig.warning;

            return {
                name: item.name,
                value: [...coords, item.value], // [经度, 纬度, 数值]
                itemStyle: { color: itemColor }
            };
        });

        // --- 配置图表选项 ---
        const option = {
            tooltip: {
                trigger: 'item',
                formatter: function (params) {
                    const data = warehouseDataConfig.find(d => d.name === params.name);
                    const ratio = ((data.value / data.capacity) * 100).toFixed(1);
                    return `
                        <b>${params.name}</b><br/>
                        体积: ${data.value} m³<br/>
                        容量: ${data.capacity} m³<br/>
                        占用率: ${ratio}% ${ratio > 80 ? '<span style="color:red">[高]</span>' : ''}
                    `;
                }
            },
            geo: {
                map: 'New Zealand',
                roam: true, // 允许缩放和平移
                zoom: mapConfig.zoom,
                center: mapConfig.center,
                itemStyle: {
                    areaColor: '#f0f2f5',
                    borderColor: '#999'
                },
                emphasis: { // 鼠标悬停时的样式
                    itemStyle: { areaColor: '#ddd' }
                }
            },
            series: [
                {
                    name: '仓库',
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: seriesData,
                    symbolSize: function (val) {
                        // 气泡大小根据体积调整
                        return Math.sqrt(val[2]) / 10; // 开根号避免气泡过大
                    },
                    label: {
                        show: true,
                        position: 'right',
                        formatter: '{b}'
                    }
                }
            ]
        };

        myChart.setOption(option);
        
        // 自适应窗口大小
        window.addEventListener('resize', () => myChart.resize());

    } catch (error) {
        console.error('地图初始化失败:', error);
        chartDom.innerHTML = '<p style="color:red; text-align:center;">地图加载失败，请检查网络或控制台报错。</p>';
    }
});