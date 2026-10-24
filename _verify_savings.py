"""Ad-hoc verification for the POST /savings handler (task 1.3)."""
import json
import threading
import urllib.request

from energy_cost_predictor.service import make_server


def main() -> None:
    server = make_server(port=0)
    host, port = server.server_address[:2]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        payload = {
            "house": {
                "house_id": "ma-reference-001",
                "region_key": "MA",
                "heated_area_sqft": 2000.0,
                "ua_coefficient": 0.00004,
                "setpoint_f": 68.0,
                "conventional_fuel": "heating_oil",
                "system_efficiency": 0.9,
            },
            "period": {"start": "2023-01-01", "end": "2023-12-31"},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}:{port}/savings",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            body = json.loads(resp.read().decode("utf-8"))
        print("status:", status)
        print("body:", body)

        assert status == 200, status
        assert set(body) == {
            "conventional_cost",
            "heat_pump_cost",
            "savings",
            "fallbacks",
        }, body
        for k in ("conventional_cost", "heat_pump_cost", "savings"):
            v = body[k]
            assert isinstance(v, (int, float)), (k, v)
            assert round(v, 2) == v, (k, v)
        assert round(body["conventional_cost"] - body["heat_pump_cost"], 2) == body[
            "savings"
        ], body
        assert body["fallbacks"] == {"weather": True, "pricing": True}, body

        # Non-annual period -> engine InvalidPeriodError -> dispatcher 500 for now
        # (status mapping to 400 is task 1.5). Confirm it does not 200.
        bad = dict(payload)
        bad = {**payload, "period": {"start": "2023-01-01", "end": "2023-06-30"}}
        req2 = urllib.request.Request(
            f"http://{host}:{port}/savings",
            data=json.dumps(bad).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req2) as resp2:
                print("non-annual status:", resp2.status, resp2.read())
                raise AssertionError("expected non-200 for non-annual period")
        except urllib.error.HTTPError as e:
            print("non-annual status:", e.code, e.read().decode())
            assert e.code != 200

        print("OK: /savings handler verified")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
