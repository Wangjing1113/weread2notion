"""
微信读书划线笔记导出为 Markdown 文件
- 每本书生成一个 .md 文件，保存到 markdown/ 目录
- 包含书籍信息、阅读状态、章节划线和笔记
"""

import json
import os
import re
import time
import requests
from requests.utils import cookiejar_from_dict
from http.cookies import SimpleCookie
from datetime import datetime
from dotenv import load_dotenv
from retrying import retry

load_dotenv()

# ===== 微信读书 API 地址 =====
WEREAD_URL = "https://weread.qq.com/"
WEREAD_NOTEBOOKS_URL = "https://weread.qq.com/api/user/notebook"
WEREAD_BOOKMARKLIST_URL = "https://weread.qq.com/web/book/bookmarklist"
WEREAD_CHAPTER_INFO = "https://weread.qq.com/web/book/chapterInfos"
WEREAD_READ_INFO_URL = "https://weread.qq.com/web/book/readinfo"
WEREAD_REVIEW_LIST_URL = "https://weread.qq.com/web/review/list"
WEREAD_BOOK_INFO = "https://weread.qq.com/web/book/info"

# ===== Markdown 文件保存目录 =====
OUTPUT_DIR = "markdown"


def parse_cookie_string(cookie_string):
    """把 Cookie 字符串解析成 requests 能用的格式"""
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {}
    cookiejar = None
    for key, morsel in cookie.items():
        cookies_dict[key] = morsel.value
        cookiejar = cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)
    return cookiejar


def refresh_token(exception):
    """Cookie 失效时尝试刷新"""
    session.get(WEREAD_URL)


@retry(stop_max_attempt_number=3, wait_fixed=5000, retry_on_exception=refresh_token)
def get_bookmark_list(bookId):
    """获取某本书的所有划线"""
    session.get(WEREAD_URL)
    params = dict(bookId=bookId)
    r = session.get(WEREAD_BOOKMARKLIST_URL, params=params)
    if r.ok:
        updated = r.json().get("updated")
        # 按章节和位置排序，确保划线顺序正确
        updated = sorted(
            updated,
            key=lambda x: (x.get("chapterUid", 1), int(x.get("range").split("-")[0])),
        )
        return updated
    return []


@retry(stop_max_attempt_number=3, wait_fixed=5000, retry_on_exception=refresh_token)
def get_read_info(bookId):
    """获取阅读进度信息（是否读完、阅读时长等）"""
    session.get(WEREAD_URL)
    params = dict(bookId=bookId, readingDetail=1, readingBookIndex=1, finishedDate=1)
    r = session.get(WEREAD_READ_INFO_URL, params=params)
    if r.ok:
        return r.json()
    return None


@retry(stop_max_attempt_number=3, wait_fixed=5000, retry_on_exception=refresh_token)
def get_bookinfo(bookId):
    """获取书的详细信息（ISBN、评分）"""
    session.get(WEREAD_URL)
    params = dict(bookId=bookId)
    r = session.get(WEREAD_BOOK_INFO, params=params)
    if r.ok:
        data = r.json()
        isbn = data.get("isbn", "")
        rating = data.get("newRating", 0) / 1000
        return (isbn, rating)
    return ("", 0)


@retry(stop_max_attempt_number=3, wait_fixed=5000, retry_on_exception=refresh_token)
def get_review_list(bookId):
    """获取笔记（手写的批注）"""
    session.get(WEREAD_URL)
    params = dict(bookId=bookId, listType=11, mine=1, syncKey=0)
    r = session.get(WEREAD_REVIEW_LIST_URL, params=params)
    if r.ok:
        reviews = r.json().get("reviews", [])
        # summary 是书评（type=4），reviews 是章节笔记（type=1）
        summary = list(filter(lambda x: x.get("review").get("type") == 4, reviews))
        reviews = list(filter(lambda x: x.get("review").get("type") == 1, reviews))
        reviews = list(map(lambda x: x.get("review"), reviews))
        reviews = list(map(lambda x: {**x, "markText": x.pop("content")}, reviews))
        return summary, reviews
    return [], []


@retry(stop_max_attempt_number=3, wait_fixed=5000, retry_on_exception=refresh_token)
def get_chapter_info(bookId):
    """获取章节信息"""
    session.get(WEREAD_URL)
    body = {"bookIds": [bookId], "synckeys": [0], "teenmode": 0}
    r = session.post(WEREAD_CHAPTER_INFO, json=body)
    if (
        r.ok
        and "data" in r.json()
        and len(r.json()["data"]) == 1
        and "updated" in r.json()["data"][0]
    ):
        update = r.json()["data"][0]["updated"]
        return {item["chapterUid"]: item for item in update}
    return None


def get_notebooklist():
    """获取有划线/笔记的书籍列表"""
    session.get(WEREAD_URL)
    r = session.get(WEREAD_NOTEBOOKS_URL)
    if r.ok:
        data = r.json()
        books = data.get("books", [])
        books.sort(key=lambda x: x["sort"])
        return books
    else:
        print(r.text)
    return None


def sanitize_filename(name):
    """清理文件名，去掉不能用作文件名的特殊字符"""
    # 去掉 / \ : * ? " < > | 等特殊字符
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    # 去掉首尾空格
    name = name.strip()
    return name


def format_reading_time(seconds):
    """把阅读秒数转换成可读的时间格式"""
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    result = ""
    if hours > 0:
        result += f"{hours}小时"
    if minutes > 0:
        result += f"{minutes}分钟"
    return result if result else "不到1分钟"


def generate_markdown(title, author, isbn, rating, read_info, chapter, bookmark_list, summary):
    """把一本书的所有信息组装成 Markdown 格式的文本"""
    lines = []

    # ===== 书名和作者 =====
    lines.append(f"# {title}")
    lines.append(f"> 作者：{author}")
    lines.append("")

    # ===== 阅读信息 =====
    if read_info:
        status = "读完" if read_info.get("markedStatus") == 4 else "在读"
        progress = read_info.get("readingProgress", 0)
        reading_time = format_reading_time(read_info.get("readingTime", 0))
        lines.append(f"- **状态**：{status}")
        lines.append(f"- **进度**：{progress}%")
        lines.append(f"- **阅读时长**：{reading_time}")
        if "finishedDate" in read_info:
            finished = datetime.utcfromtimestamp(read_info["finishedDate"]).strftime("%Y-%m-%d")
            lines.append(f"- **读完日期**：{finished}")
        lines.append("")

    # ===== 划线和笔记（按章节分组）=====
    if bookmark_list:
        lines.append("---")
        lines.append("")

        if chapter:
            # 按章节分组展示
            d = {}
            for data in bookmark_list:
                chapterUid = data.get("chapterUid", 1)
                if chapterUid not in d:
                    d[chapterUid] = []
                d[chapterUid].append(data)

            for chapterUid, marks in d.items():
                # 章节标题
                if chapterUid in chapter:
                    chapter_title = chapter[chapterUid].get("title", "")
                    level = chapter[chapterUid].get("level", 1)
                    # 用 ## 或 ### 表示章节层级
                    prefix = "#" * (level + 1)
                    lines.append(f"{prefix} {chapter_title}")
                    lines.append("")

                # 该章节下的划线
                for mark in marks:
                    mark_text = mark.get("markText", "")
                    # 判断是划线还是笔记
                    if mark.get("reviewId"):
                        # 这是手写笔记，用 ✍️ 标记
                        lines.append(f"- ✍️ {mark_text}")
                    else:
                        # 这是划线内容
                        lines.append(f"- {mark_text}")
                lines.append("")
        else:
            # 没有章节信息，直接列出所有划线
            for mark in bookmark_list:
                mark_text = mark.get("markText", "")
                if mark.get("reviewId"):
                    lines.append(f"- ✍️ {mark_text}")
                else:
                    lines.append(f"- {mark_text}")
            lines.append("")

    # ===== 书评（如果有）=====
    if summary and len(summary) > 0:
        lines.append("---")
        lines.append("")
        lines.append("## 书评")
        lines.append("")
        for item in summary:
            content = item.get("review", {}).get("content", "")
            lines.append(f"> {content}")
            lines.append("")

    return "\n".join(lines)


def get_cookie():
    """获取微信读书的 Cookie（和 weread.py 逻辑一样）"""
    url = os.getenv("CC_URL")
    if not url:
        url = "https://cookiecloud.malinkang.com/"
    id = os.getenv("CC_ID")
    password = os.getenv("CC_PASSWORD")
    cookie = os.getenv("WEREAD_COOKIE")
    if url and id and password:
        cookie = try_get_cloud_cookie(url, id, password)
    if not cookie or not cookie.strip():
        raise Exception("没有找到cookie，请按照文档填写cookie")
    return cookie


def try_get_cloud_cookie(url, id, password):
    """从 CookieCloud 获取 Cookie"""
    if url.endswith("/"):
        url = url[:-1]
    req_url = f"{url}/get/{id}"
    data = {"password": password}
    response = requests.post(req_url, data=data)
    if response.status_code == 200:
        data = response.json()
        cookie_data = data.get("cookie_data")
        if cookie_data and "weread.qq.com" in cookie_data:
            cookies = cookie_data["weread.qq.com"]
            cookie_str = "; ".join(
                [f"{cookie['name']}={cookie['value']}" for cookie in cookies]
            )
            return cookie_str
    return None


if __name__ == "__main__":
    # ===== 初始化 =====
    weread_cookie = get_cookie()
    session = requests.Session()
    session.cookies = parse_cookie_string(weread_cookie)
    session.get(WEREAD_URL)

    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ===== 获取书籍列表 =====
    books = get_notebooklist()
    if books is None or len(books) == 0:
        print("没有获取到书籍列表，请检查 Cookie 是否有效")
        exit(1)

    print(f"共找到 {len(books)} 本有笔记的书，开始导出...")

    for index, book_data in enumerate(books):
        book = book_data.get("book")
        title = book.get("title")
        bookId = book.get("bookId")
        author = book.get("author", "未知")

        print(f"正在导出 《{title}》({index + 1}/{len(books)})")

        # 获取书籍详情
        isbn, rating = get_bookinfo(bookId)

        # 获取阅读进度
        read_info = get_read_info(bookId)

        # 获取章节信息
        chapter = get_chapter_info(bookId)

        # 获取划线
        bookmark_list = get_bookmark_list(bookId)

        # 获取笔记
        summary, reviews = get_review_list(bookId)

        # 合并划线和笔记，按章节和位置排序
        bookmark_list.extend(reviews)
        bookmark_list = sorted(
            bookmark_list,
            key=lambda x: (
                x.get("chapterUid", 1),
                (
                    0
                    if (x.get("range", "") == "" or x.get("range").split("-")[0] == "")
                    else int(x.get("range").split("-")[0])
                ),
            ),
        )

        # 生成 Markdown 内容
        md_content = generate_markdown(
            title, author, isbn, rating, read_info, chapter, bookmark_list, summary
        )

        # 保存文件
        filename = sanitize_filename(title) + ".md"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

    print(f"全部导出完成！共 {len(books)} 本书，保存在 {OUTPUT_DIR}/ 目录下")
