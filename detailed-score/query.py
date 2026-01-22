import json
import os
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple

import requests

# 请将抓包到的cookie填入此处（原样粘贴整段 Cookie 头的值）
COOKIE_STR = "EMAP_LANG=zh; THEME=magenta; _WEU=This is a sample"

os.environ["NO_PROXY"] = "ehall.szu.edu.cn"

URL_SCORE = "https://ehall.szu.edu.cn:443/jwapp/sys/cjcx/modules/cjcx/xscjcx.do"

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/536.35",
    "Origin": "https://ehall.szu.edu.cn",
    "Referer": "https://ehall.szu.edu.cn/jwapp/sys/cjcx/*default/index.do",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

PAGE_SIZE = 100
TIMEOUT = 15

# JXBID -> 课程记录
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
            "KCM": row.get("KCM"),
            "PSCJ": None,
            "QMCJ": None,
            "PSCJXS": None,
            "QMCJXS": None,
            "ZCJ": row.get("ZCJ"),
        }
        course_map[jxbid] = c
    else:
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


def fill_all_coefficients(session: requests.Session) -> None:
    """
    通过 xscjcx.do 间接批量获取所有课程的 PSCJXS/QMCJXS：
    - PSCJXS、QMCJXS 都是 10 的整数倍，且和为 100
    - 因此只需要枚举 PSCJXS=0..100（步长10）共 11 次查询
    - 每次返回 rows 后，按 JXBID 更新 course_map
    """
    for psxs in range(0, 101, 10):
        query_setting = json.dumps(
            [{"name": "PSCJXS", "value": str(psxs), "linkOpt": "and", "builder": "equal"}],
            ensure_ascii=False,
        )

        data = {
            "querySetting": query_setting,
            "pageSize": str(PAGE_SIZE),
            "pageNumber": "1",
        }

        r = session.post(URL_SCORE, data=data, timeout=TIMEOUT)
        r.raise_for_status()
        rows = safe_rows_score(r.text)

        for row in rows:
            course = upsert_course(row)
            if not course:
                continue
            course["PSCJXS"] = psxs
            course["QMCJXS"] = 100 - psxs


if __name__ == "__main__":
    try:
        session = build_session(COOKIE_STR)
    except Exception as e:
        print(f"初始化失败：{e}")
        raise SystemExit(-1)

    for score in range(0, 101):
        query_by_score(session, "PSCJ", score)
        query_by_score(session, "QMCJ", score)
        if score % 10 == 0 or score == 100:
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
