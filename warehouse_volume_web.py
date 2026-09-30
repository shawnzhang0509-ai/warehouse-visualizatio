"""仓库容积率 Web 看板（Flask）。桌面 panel 可调用 warehouse_volume 模块；浏览器打开本服务查看地图。"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import warehouse_volume as wv

ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "warehouse_volume.html"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(ROOT), static_url_path="")

    @app.route("/")
    @app.route("/volume")
    def volume_page():
        if HTML_FILE.is_file():
            return HTML_FILE.read_text(encoding="utf-8")
        return "<h1>缺少 warehouse_volume.html</h1>", 404

    @app.route("/nz.json")
    def nz_geo():
        return send_from_directory(ROOT, "nz.json")

    @app.route("/api/volume/channels")
    def api_channels():
        region = request.args.get("region", "NZ")
        try:
            data = wv.list_channels(region)
            return jsonify({"status": "success", "region": wv.normalize_region(region), "data": data})
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.route("/api/volume/report")
    def api_report():
        region = request.args.get("region", "NZ")
        channels = request.args.getlist("channel")
        if not channels:
            raw = request.args.get("channels", "")
            channels = [c.strip() for c in raw.split(",") if c.strip()]
        try:
            report = wv.build_volume_report(region, channels or None)
            report["mapMarkers"] = wv.map_markers_payload(report)
            return jsonify(report)
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(__import__("os").environ.get("WAREHOUSE_VOLUME_PORT", "5001"))
    print(f"仓库容积率看板: http://127.0.0.1:{port}/volume")
    app.run(host="0.0.0.0", port=port, debug=False)
