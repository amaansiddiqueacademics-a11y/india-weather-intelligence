"""Warehouse explorer: table profiles, chart data and 'follow the keys' row traces (star schema only)."""
from __future__ import annotations

import psycopg

NUM_TYPES = ("real", "double precision", "integer", "smallint", "bigint", "numeric")


def sizes(c: psycopg.Connection) -> dict[str, float]:
    rows = c.execute("""SELECT cl.relname, pg_total_relation_size(cl.oid)/1048576.0 AS mb FROM pg_class cl
        JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='star' AND cl.relkind='r'""").fetchall()
    return {r["relname"]: round(float(r["mb"]), 2) for r in rows}


def weather_columns(c: psycopg.Connection, cols: list[str]) -> list[dict]:
    """Fill rate and min / average / max of every numeric column of the hourly fact (one pass over the table)."""
    parts = ", ".join(f"count({x}) AS n_{i}, min({x})::float AS lo_{i}, avg({x})::float AS av_{i}, max({x})::float AS hi_{i}" for i, x in enumerate(cols))
    r = c.execute(f"SELECT count(*) AS total, {parts} FROM star.fact_weather_hourly").fetchone()
    return [dict(name=x, fill_pct=100 * r[f"n_{i}"] / r["total"], lo=r[f"lo_{i}"], avg=r[f"av_{i}"], hi=r[f"hi_{i}"]) for i, x in enumerate(cols)]


def charts(c: psycopg.Connection) -> dict:
    q = lambda sql: c.execute(sql).fetchall()  # noqa: E731
    return dict(
        by_city=q("""SELECT l.city, l.state, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) GROUP BY 1,2 ORDER BY 3 DESC, 1"""),
        by_season=q("""SELECT d.imd_season AS label, count(*) AS n, avg(f.temperature_c)::float AS temp, sum(f.precipitation_mm)::float/count(DISTINCT d.year)/10 AS rain_per_year_mm_per_city
                       FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key) GROUP BY 1 ORDER BY 2 DESC"""),
        by_part=q("""SELECT t.part_of_day AS label, count(*) AS n, avg(f.temperature_c)::float AS temp
                     FROM star.fact_weather_hourly f JOIN star.dim_time t USING (time_key) GROUP BY 1 ORDER BY avg(f.temperature_c) DESC"""),
        by_year=q("""SELECT d.year, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key) GROUP BY 1 ORDER BY 1"""),
        pred_by_module=q("""SELECT m.risk_module AS label, count(*) AS n FROM star.fact_prediction p JOIN star.dim_model m USING (model_key) GROUP BY 1 ORDER BY 2 DESC"""),
        pred_by_model=q("""SELECT m.model_name AS label, m.model_family AS family, count(*) AS n FROM star.fact_prediction p JOIN star.dim_model m USING (model_key) GROUP BY 1,2 ORDER BY 3 DESC"""),
        agri_by_type=q("""SELECT cr.crop_type AS label, count(*) AS n FROM star.fact_agri_production a JOIN star.dim_crop cr USING (crop_key) GROUP BY 1 ORDER BY 2 DESC"""),
        eval_by_metric=q("""SELECT metric_name AS label, count(*) AS n FROM star.fact_model_evaluation GROUP BY 1 ORDER BY 2 DESC"""),
        date_by_season=q("""SELECT imd_season AS label, count(*) AS n FROM star.dim_date GROUP BY 1 ORDER BY 2 DESC"""),
        loc_elev=q("""SELECT city, elevation_m::float AS elev, latitude::float AS lat FROM star.dim_location ORDER BY elevation_m DESC"""),
        time_pod=q("""SELECT part_of_day AS label, count(*) AS n FROM star.dim_time GROUP BY 1 ORDER BY min(time_key)"""),
        crop_by_type=q("""SELECT crop_type AS label, count(*) AS n FROM star.dim_crop GROUP BY 1 ORDER BY 2 DESC"""),
        model_by_family=q("""SELECT model_family AS label, count(*) AS n FROM star.dim_model GROUP BY 1 ORDER BY 2 DESC"""),
        crop_season=q("""SELECT season_name AS label, 1 AS n FROM star.dim_crop_season ORDER BY 1"""),
        flags=q("""SELECT sum(is_imputed::int) AS imputed, sum(is_outlier::int) AS outliers, count(*) AS total FROM star.fact_weather_hourly""")[0],
    )


def trace(c: psycopg.Connection, schema: dict, table: str, pick: str, location: str | None, n: int) -> dict:
    """One real fact row plus the dimension row behind each of its foreign keys."""
    t = next(x for x in schema["tables"] if x["name"] == table)
    if t["kind"] != "fact":
        raise ValueError("trace works on fact tables")
    pk = [x["name"] for x in t["columns"] if x["pk"]]
    where, params, order = "", [], ", ".join(pk)
    if table == "fact_weather_hourly":
        loc = c.execute("SELECT location_key FROM star.dim_location WHERE location_id=%s", (location or "BOM",)).fetchone()
        if not loc:
            raise ValueError("unknown city")
        where, params = "WHERE location_key=%s", [loc["location_key"]]
        order = {"wettest": "precipitation_mm DESC NULLS LAST", "hottest": "temperature_c DESC NULLS LAST", "coldest": "temperature_c ASC NULLS LAST",
                 "humid": "heat_index_c DESC NULLS LAST"}.get(pick, order)
        n = 0 if pick in ("wettest", "hottest", "coldest", "humid") else n * 9973  # "another row" hops through the table
    else:
        n = n * 37
    row = c.execute(f"SELECT * FROM star.{table} {where} ORDER BY {order} LIMIT 1 OFFSET %s", (*params, n)).fetchone()
    if not row:
        raise ValueError("no such row")
    lookups = []
    for col in t["columns"]:
        if not col["fk"]:
            continue
        dim, dkey = col["fk"].split(".")
        d = c.execute(f"SELECT * FROM star.{dim} WHERE {dkey}=%s", (row[col["name"]],)).fetchone()
        v = row[col["name"]]
        near = c.execute(f"""(SELECT * FROM star.{dim} WHERE {dkey} < %s ORDER BY {dkey} DESC LIMIT 2) UNION ALL (SELECT * FROM star.{dim} WHERE {dkey} >= %s ORDER BY {dkey} LIMIT 3) ORDER BY {dkey}""", (v, v)).fetchall()  # neighbours
        lookups.append(dict(column=col["name"], value=v, table=dim, key=dkey, row=d, near=near))
    sql = f"SELECT f.*, {', '.join(f'd{i}.*' for i in range(len(lookups)))}\nFROM star.{table} f\n" + "\n".join(f"JOIN star.{x['table']} d{i} ON d{i}.{x['key']} = f.{x['column']}" for i, x in enumerate(lookups)) \
        + "\nWHERE " + " AND ".join(f"f.{k} = {row[k]!r}" for k in pk) + ";"
    context = []
    if table == "fact_weather_hourly":  # the hours either side, for context
        context = c.execute("SELECT * FROM star.fact_weather_hourly WHERE location_key=%s AND date_key=%s AND time_key BETWEEN %s AND %s ORDER BY time_key",
                            (row["location_key"], row["date_key"], max(0, row["time_key"] - 1), min(23, row["time_key"] + 1))).fetchall()
    return dict(table=table, pk=pk, row=row, lookups=lookups, sql=sql, context=context)
