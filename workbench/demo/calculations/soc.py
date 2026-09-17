import math

def calculate_weighted_soc(rows):
    if not rows:
        raise ValueError("没有可计算的电池簇")
    for row in rows:
        for key in ("soc_pct", "capacity_basis_kwh"):
            v = row[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError("输入缺失或不是有限数值")
        if not 0 <= row["soc_pct"] <= 100 or row["capacity_basis_kwh"] <= 0:
            raise ValueError("SOC范围或容量基准无效")
    return sum(r["soc_pct"] * r["capacity_basis_kwh"] for r in rows) / sum(r["capacity_basis_kwh"] for r in rows)
