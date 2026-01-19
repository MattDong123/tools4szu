import json
import os
import time
from typing import Dict, Any, List, Optional, Tuple

import requests

# 请将抓包到的cookie填入此处（原样粘贴整段 Cookie 头的值）
COOKIE_STR = "EMAP_LANG=zh; THEME=magenta; _WEU=This is a sample"

os.environ["NO_PROXY"] = "ehall.szu.edu.cn"

# 成绩查询接口
URL_SCORE = "https://ehall.szu.edu.cn:443/jwapp/sys/cjcx/modules/cjcx/xscjcx.do"
# 系数查询接口（按教学班ID查询）
URL_COEFF = "https://ehall.szu.edu.cn:443/jwapp/sys/cjcx/modules/cjcx/jxblrcjxs.do"

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

# 用 dict 存课程：JXBID -> 课程记录（主键改为 JXBID）
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


def safe_rows_score(resp_text: str) -> List[Dict[str, Any]]:
    obj = json.loads(resp_text)
    return obj["datas"]["xscjcx"]["rows"]


def safe_rows_coeff(resp_text: str) -> List[Dict[str, Any]]:
    obj = json.loads(resp_text)
    # 你提供的结构：datas.jxblrcjxs.rows[0]
    return obj["datas"]["jxblrcjxs"]["rows"]


def upsert_course(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    以 JXBID 做主键更新；未出现则初始化。
    同时保存课程名 KCM 以便输出。
    """
    jxbid = row.get("JXBID")
    if not jxbid:
        return None

    c = course_map.get(jxbid)
    if not c:
        c = {
            "JXBID": jxbid,
            "KCM": row.get("KCM"),   # 课程名也存一下
            "PSCJ": None,
            "QMCJ": None,
            "PSCJXS": None,
            "QMCJXS": None,
            "ZCJ": row.get("ZCJ"),
        }
        course_map[jxbid] = c
    else:
        # KCM / ZCJ 如有返回则更新（不同查询返回字段齐全度可能不同）
        if "KCM" in row and row.get("KCM") not in (None, ""):
            c["KCM"] = row.get("KCM")
        if "ZCJ" in row and row.get("ZCJ") not in (None, ""):
            c["ZCJ"] = row.get("ZCJ")

    return c


def post_query_score(session: requests.Session, query_setting: str) -> List[Dict[str, Any]]:
    """成绩查询：不分页（每次 pageNumber=1），pageSize=100 足够覆盖个人课程"""
    data = {
        "querySetting": query_setting,
        "pageSize": str(PAGE_SIZE),
        "pageNumber": "1",
    }
    r = session.post(URL_SCORE, data=data, timeout=TIMEOUT)
    r.raise_for_status()
    return safe_rows_score(r.text)


def query_by_score(session: requests.Session, field: str, score: int, max_retries: int = 2) -> None:
    """
    field: 'PSCJ' 或 'QMCJ'
    score: 0..100
    说明：成绩获取仍保持轮询逻辑不变（直接查通常 PSCJ/QMCJ 为空）。
    """
    if field not in ("PSCJ", "QMCJ"):
        raise ValueError("field 必须为 'PSCJ' 或 'QMCJ'")

    query_setting = json.dumps(
        [{"name": field, "value": str(score), "linkOpt": "and", "builder": "equal"}],
        ensure_ascii=False,
    )

    last_err = None
    rows: List[Dict[str, Any]] = []
    for _ in range(max_retries + 1):
        try:
            rows = post_query_score(session, query_setting)
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
        if "ZCJ" in row and row.get("ZCJ") not in (None, ""):
            course["ZCJ"] = row.get("ZCJ")


def fetch_coefficients(session: requests.Session, jxbid: str, max_retries: int = 2) -> Tuple[Optional[Any], Optional[Any]]:
    """
    通过教学班ID获取成绩系数：
    POST /modules/cjcx/jxblrcjxs.do
    参数：JXBID, XSYC=0
    返回：datas.jxblrcjxs.rows[0] -> PSCJXS, QMCJXS
    """
    data = {
        "JXBID": jxbid,
        "XSYC": "0",
    }

    last_err = None
    resp_rows: List[Dict[str, Any]] = []
    for _ in range(max_retries + 1):
        try:
            r = session.post(URL_COEFF, data=data, timeout=TIMEOUT)
            r.raise_for_status()
            resp_rows = safe_rows_coeff(r.text)
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(0.4)

    if last_err is not None:
        print(f"[WARN] JXBID={jxbid} 系数查询失败：{last_err}")
        return None, None

    if not resp_rows:
        return None, None

    row0 = resp_rows[0] or {}
    return row0.get("PSCJXS"), row0.get("QMCJXS")


def fill_all_coefficients(session: requests.Session) -> None:
    """遍历已收集到的课程（按 JXBID）逐个拉取 PSCJXS/QMCJXS。"""
    for jxbid, c in course_map.items():
        psxs, qmxs = fetch_coefficients(session, jxbid)
        c["PSCJXS"] = psxs
        c["QMCJXS"] = qmxs


if __name__ == "__main__":
    try:
        session = build_session(COOKIE_STR)
    except Exception as e:
        print(f"初始化失败：{e}")
        raise SystemExit(-1)

    for score in range(0, 101):
        query_by_score(session, "PSCJ", score)
        query_by_score(session, "QMCJ", score)
        if score % 5 == 0 or score == 100:
            print(f"当前进度：{score}%")

    fill_all_coefficients(session)

    print("=====================================")
    # 输出：用课程名 KCM（并可附带 JXBID 便于区分重名课）
    for jxbid in sorted(course_map.keys()):
        c = course_map[jxbid]
        kcm = c.get("KCM")
        ps = c.get("PSCJ")
        qm = c.get("QMCJ")
        zc = c.get("ZCJ")
        psxs = c.get("PSCJXS")
        qmxs = c.get("QMCJXS")
        print(
            f"{kcm} (教学班ID={jxbid}): 平时成绩系数{psxs}, 平时成绩{ps}, 期末成绩系数{qmxs}, 期末成绩{qm}, 总评{zc}"
        )
