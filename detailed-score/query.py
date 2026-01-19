import json
import os
import time
from typing import Dict, Any, List, Optional, Tuple

import requests

# 请将抓包到的cookie填入此处（原样粘贴整段 Cookie 头的值）
COOKIE_STR = "EMAP_LANG=zh; THEME=magenta; _WEU=this is a sample"

os.environ["NO_PROXY"] = "ehall.szu.edu.cn"
URL = "https://ehall.szu.edu.cn:443/jwapp/sys/cjcx/modules/cjcx/xscjcx.do"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Origin": "https://ehall.szu.edu.cn",
    "Referer": "https://ehall.szu.edu.cn/jwapp/sys/cjcx/*default/index.do",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

PAGE_SIZE = 100
TIMEOUT = 15

# 用 dict 存课程：KCM -> 课程记录
course_map: Dict[str, Dict[str, Any]] = {}


def parse_cookie(cookie_str: str) -> Dict[str, str]:
    """更稳健的 cookie 解析：split('=', 1) 防止值里含 '='"""
    cookies: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def build_session(cookie_str: str) -> requests.Session:
    if not cookie_str.strip():
        raise ValueError("COOKIE_STR 为空：请先填入抓包到的 cookie。")

    s = requests.Session()
    s.headers.update(HEADERS)
    s.cookies.update(parse_cookie(cookie_str))
    return s


def safe_rows(resp_text: str) -> List[Dict[str, Any]]:
    obj = json.loads(resp_text)
    return obj["datas"]["xscjcx"]["rows"]


def upsert_course(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """以 KCM 做主键更新；未出现则初始化。"""
    kcm = row.get("KCM")
    if not kcm:
        return None

    c = course_map.get(kcm)
    if not c:
        c = {
            "KCM": kcm,
            "PSCJ": None,
            "QMCJ": None,
            "PSCJXS": None,  # 反推后填
            "QMCJXS": None,  # 反推后填
            "ZCJ": row.get("ZCJ"),
            "XS_SOLUTIONS": None,  # 若多解，存所有解
        }
        course_map[kcm] = c
    else:
        # ZCJ 如有返回则更新（有时不同查询返回字段齐全度不同）
        if "ZCJ" in row and row.get("ZCJ") not in (None, ""):
            c["ZCJ"] = row.get("ZCJ")
    return c


def post_query(session: requests.Session, query_setting: str) -> List[Dict[str, Any]]:
    """不分页：每个分数只查 pageNumber=1"""
    data = {
        "querySetting": query_setting,
        "pageSize": str(PAGE_SIZE),
        "pageNumber": "1",
    }
    r = session.post(URL, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    return safe_rows(r.text)


def query_by_score(session: requests.Session, field: str, score: int, max_retries: int = 2) -> None:
    """
    field: 'PSCJ' 或 'QMCJ'
    score: 0..100
    说明：接口不返回系数了，这里只记录分数和 ZCJ。
    """
    if field not in ("PSCJ", "QMCJ"):
        raise ValueError("field 必须为 'PSCJ' 或 'QMCJ'")

    query_setting = json.dumps(
        [{"name": field, "value": str(score), "linkOpt": "and", "builder": "equal"}],
        ensure_ascii=False,
    )

    last_err = None
    for _ in range(max_retries + 1):
        try:
            rows = post_query(session, query_setting)
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(0.4)

    if last_err is not None:
        print(f"[WARN] {field}={score} 查询失败：{last_err}")
        return

    for row in rows:
        course = upsert_course(row)
        if not course:
            continue
        course[field] = score
        # ZCJ 可能在这里返回，顺便更新
        if "ZCJ" in row and row.get("ZCJ") not in (None, ""):
            course["ZCJ"] = row.get("ZCJ")


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def infer_coefficients(ps: float, qm: float, zcj: float) -> List[Tuple[int, int, float]]:
    """
    返回所有满足条件的 (pscjxs, qmcjxs, computed_zcj)。
    优先假设：加权公式 (ps*a + qm*b)/100 = zcj 且 a+b=100
    若无解：放宽为不要求 a+b=100（仍按 /100）
    若仍无解：再尝试不除以 100 的直乘（极少见，作为兜底）
    """
    coeffs = list(range(0, 101, 10))
    sols: List[Tuple[int, int, float]] = []
    tol = 0.51

    def match(v: float, target: float) -> bool:
        # 允许非常小的浮点误差；也兼容 ZCJ 是整数但计算值有 0.5 等情况（按常见显示不会四舍五入）
        return abs(v - target) < 0.01 + tol

    for a in coeffs:
        b = 100 - a
        if b not in coeffs:
            continue
        v = (ps * a + qm * b) / 100.0
        if match(v, zcj):
            sols.append((a, b, v))
    if sols:
        return sols
    return sols


def solve_all_coefficients() -> None:
    """在已拿到 PSCJ/QMCJ/ZCJ 后，逐门课反推 PSCJXS/QMCJXS。"""
    for c in course_map.values():
        ps = _to_float(c.get("PSCJ"))
        qm = _to_float(c.get("QMCJ"))
        zc = _to_float(c.get("ZCJ"))

        if ps is None or qm is None or zc is None:
            c["XS_SOLUTIONS"] = []
            continue

        sols = infer_coefficients(ps, qm, zc)
        c["XS_SOLUTIONS"] = sols

        if len(sols) == 1:
            a, b, _ = sols[0]
            c["PSCJXS"] = a
            c["QMCJXS"] = b
        else:
            c["PSCJXS"] = None
            c["QMCJXS"] = None


if __name__ == "__main__":
    try:
        session = build_session(COOKIE_STR)
    except Exception as e:
        print(f"初始化失败：{e}")
        exit(-1)

    for score in range(0, 101):
        query_by_score(session, "PSCJ", score)
        query_by_score(session, "QMCJ", score)

        if score % 5 == 0 or score == 100:
            print(f"当前进度：{score}%")

    # 再基于 PSCJ/QMCJ/ZCJ 反推系数
    solve_all_coefficients()

    print("=====================================")
    for kcm in course_map.keys():
        c = course_map[kcm]
        if c["PSCJXS"] is not None and c["QMCJXS"] is not None:
            print(
                f"{c['KCM']}: 平时系数{c['PSCJXS']}, 平时{c['PSCJ']}, "
                f"期末系数{c['QMCJXS']}, 期末{c['QMCJ']}, 总评{c['ZCJ']}"
            )
        else:
            print(
                f"{c['KCM']}: 平时{c['PSCJ']}, 期末{c['QMCJ']}, 总评{c['ZCJ']}"
            )
