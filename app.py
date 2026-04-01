from flask import Flask, jsonify, render_template_string
import pyodbc

app = Flask(__name__)

# 1. 配置数据库连接信息
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=if-akl-live.database.windows.net;"
    "DATABASE=nz_ierp_live;"
    "UID=nzlivepooluser;"
    "PWD=iFur3RP@5sc^l^t3!;"
)

# --- 新增：读取 HTML 文件内容 ---
# 确保 index.html 和 app.py 在同一目录
try:
    with open('index.html', 'r', encoding='utf-8') as f:
        html_content = f.read()
except FileNotFoundError:
    html_content = "<h1>Error: index.html not found</h1>"

# --- 新增：根路径路由 ---
# 当用户访问 http://127.0.0.1:5000/ 时，返回 HTML 页面
@app.route('/')
def index():
    return render_template_string(html_content)

# --- 关键新增：数据接口路由 ---
# 当前端请求 /get-data 时，执行 SQL 查询并返回 JSON
@app.route('/get-data')
def get_data():
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        # --- 替换为你的 SQL 语句 ---
        sql_query = """
            SELECT
                w.Name AS WarehouseName,
                SUM(s.Quantity * ISNULL(p.VolumeWithBox, 0)) AS TotalOccupiedVolume
            FROM
                dbo.Stocks s
            LEFT JOIN
                dbo.Products p ON s.ProductId = p.Id
            LEFT JOIN
                dbo.Warehouses w ON s.WarehouseId = w.Id
            GROUP BY
                w.Name
            ORDER BY
                TotalOccupiedVolume DESC;
        """
        
        cursor.execute(sql_query)
        rows = cursor.fetchall()

        # --- 处理结果 ---
        result = []
        total_volume = 0
        for row in rows:
            result.append({
                "name": row.WarehouseName,
                "value": row.TotalOccupiedVolume
            })
            total_volume += row.TotalOccupiedVolume

        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "data": result,
            "total": total_volume
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    # host='0.0.0.0' 允许外部访问
    app.run(debug=True, host='0.0.0.0', port=5000)