# ehall提前查研究生百分制成绩
import json
import os

import requests

os.environ['NO_PROXY'] = 'ehall.szu.edu.cn'

# 复制自己字符串格式的的Cookie过来，对应填进去，会自动解析成字典

cookie = '
EMAP_LANG=zh; THEME=cherry; _WEU=lWsdQHsIP*xER_SDmUkjJq_TNRaS23K6hghEBTjgHzGrD*we3PR*t_1e27ws*loPOzckvB7pJ7VNUO*vw9NP5xp_OBV2A0UbLFgZbdU6Dr9F5rQnUty2w6Sq1DCTYrBOvugJ4Uf65U_RG4kBGhI0j823B09ofOHUiRYWKT7Ku0sLlRKdsS8w9atjBMN0GZbl; amp.locale=undefined; insert_cookie=38189586; MOD_AUTH_CAS=MOD_AUTH_ST-4026046-q9KPXedkuMg0ggcp1rQmSwGFdegciapserver4; JSESSIONID=mwxowLlOOu40FeIAKmkM54BjY0I31w0_lMpCA_8-d-kBqvXNtqXX!-874731113; asessionid=39b04538-9535-45a9-9b24-d533b1f29863; route=74c501c1243c125f7a9379cabda1364b'

headers = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive',
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'Origin': 'https://ehall.szu.edu.cn',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'User-Agent': 'Mozilla/5.0',
    'X-Requested-With': 'XMLHttpRequest',
    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}
cookies = {}


def query_gte(score: int):
    data = {
        'querySetting': '[{"name":"DYBFZCJ","linkOpt":"AND","builderList":"cbl_Other","builder":"moreEqual","value":' + str(
            score) + '},{"name":"CJFZDM","linkOpt":"AND","builderList":"cbl_m_List","builder":"m_value_equal","value":"1","value_display":"百分制"}]',
        'pageSize': 99,
        'pageNumber': 1,
    }
    ret = requests.post(
        'https://ehall.szu.edu.cn/gsapp/sys/xscjglapp/modules/xscjcx/xscjcx_dqx.do',
        cookies=cookies,
        headers=headers,
        data=data
    )
    return json.loads(ret.text)['datas']['xscjcx_dqx']['rows']


def query(courseName: str):
    lScore = 0
    rScore = 100
    while lScore <= rScore:
        mid = round((lScore + rScore) / 2, 1)
        res = query_gte(mid)
        if courseName in str(res):
            lScore = mid
        else:
            rScore = mid
        if rScore - lScore <= 0.11:
            if courseName in str(query_gte(rScore)):
                return rScore
            else:
                return lScore
    return lScore


def getGrade(score: float) -> float:
    roundScore = round(score)
    if roundScore >= 90:
        return 4.0
    elif roundScore >= 85:
        return 3.5
    elif roundScore >= 80:
        return 3.0
    elif roundScore >= 75:
        return 2.5
    elif roundScore >= 70:
        return 2.0
    elif roundScore >= 65:
        return 1.5
    elif roundScore >= 60:
        return 1.0
    else:
        return 0.0


if __name__ == '__main__':
    cookie_list = cookie.split(';')
    for item in cookie_list:
        item = item.strip()
        items = item.split('=')
        cookies[items[0]] = items[1]
    coursesList = query_gte(0)
    courseNames = [{'courseName': c['KCMC'], 'score': 0, 'credit': c['XF']} for c in coursesList]
    print(f'共有 {len(courseNames)} 门百分制课程')
    for course in courseNames:
        course['score'] = query(course['courseName'])
        print(str(course['courseName']) + str('  ') + str(course["score"]))
    print('----------查询完毕----------')
    totalCredit = 0
    totalGrade = 0
    totalScore = 0
    for course in courseNames:
        totalCredit += course['credit']
        totalGrade += course['credit'] * getGrade(course['score'])
        totalScore += course['credit'] * course['score']
    print(f'总学分: {totalCredit}')
    print(f'总GPA: {round(totalGrade / totalCredit, 4)}')
    print(f'总百分制分数: {round(totalScore / totalCredit, 4)}')
