from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# -------------------------------------
# 초기 설정
# -------------------------------------

load_dotenv()

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

app = FastAPI()

# CORS (Flutter 앱에서 호출 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 필요하면 나중에 특정 도메인만 허용으로 변경 가능
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


# -------------------------------------
# 공통: 네이버 뉴스 검색 함수
# -------------------------------------

async def fetch_news(keyword: str, limit: int = 30, sort: str = "date"):
    """
    keyword: 검색 키워드 (종목명, 키워드, 자연어 등)
    limit: 최대 뉴스 개수
    sort: 'date' or 'sim'
    """

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise HTTPException(500, "NAVER API key missing (.env 확인 필요)")

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    params = {
        "query": keyword,
        "display": min(limit, 100),
        "sort": sort  # 'date' = 최신순, 'sim' = 관련도/인기순 느낌
    }

    async with httpx.AsyncClient() as client:
        res = await client.get(NAVER_NEWS_URL, headers=headers, params=params)

    if res.status_code != 200:
        raise HTTPException(res.status_code, res.text)

    data = res.json()

    articles = [
        {
            "title": item.get("title"),
            "desc": item.get("description"),
            "link": item.get("link"),
            "pubDate": item.get("pubDate"),
        }
        for item in data.get("items", [])
    ]

    return {
        "keyword": keyword,
        "count": len(articles),
        "articles": articles
    }


# -------------------------------------
# 기사 본문 크롤링 함수
# -------------------------------------

async def fetch_article_content(url: str):
    """
    네이버 뉴스 URL에서 제목 + 본문 텍스트 가져오기
    (네이버 뉴스가 아닐 경우 최대한 텍스트만 추출)
    """
    async with httpx.AsyncClient() as client:
        res = await client.get(url)

    if res.status_code != 200:
        raise HTTPException(res.status_code, f"기사 요청 실패: {res.text}")

    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    # 제목
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    content_text = ""

    # 네이버 뉴스(새 UI)의 경우 보통 #dic_area 안에 본문이 있음
    main_area = soup.select_one("#dic_area")
    if main_area:
        paragraphs = [
            p.get_text(strip=True)
            for p in main_area.find_all(["p", "span"])
            if p.get_text(strip=True)
        ]
        content_text = "\n".join(paragraphs)

    # fallback: 그래도 비어 있으면 페이지 전체에서 p 태그 긁기
    if not content_text:
        ps = soup.find_all("p")
        paragraphs = [
            p.get_text(strip=True)
            for p in ps
            if p.get_text(strip=True)
        ]
        content_text = "\n".join(paragraphs[:30])  # 너무 길어지지 않게 30개까지

    if not title and not content_text:
        raise HTTPException(500, "본문을 가져오지 못했습니다.")

    return {
        "url": url,
        "title": title,
        "content": content_text,
        "origin_link": url,
    }


# -------------------------------------
# API 엔드포인트
# -------------------------------------

@app.get("/")
def home():
    return {
        "status": "OK",
        "message": "Stock AI backend running",
        "endpoints": ["/hot-news", "/news", "/article"]
    }


# 🔹 1) 핫 뉴스 (앱 첫 화면용)
@app.get("/hot-news")
async def get_hot_news(
    limit: int = Query(5, ge=1, le=50, description="가져올 기사 개수"),
    sort: str = Query("popular", description="latest 또는 popular")
):
    """
    앱 첫 접속 시 사용:
    - 기본 5개
    - 더보기 눌렀을 때 10, 20 등으로 조절 가능
    sort:
      - latest -> 최신순 (date)
      - popular -> 인기/관련도순 느낌 (sim)
    """
    # 정렬 옵션 매핑
    if sort in ["latest", "date", "time"]:
        naver_sort = "date"
    else:  # 'popular' 또는 기타
        naver_sort = "sim"

    # 여기선 전체 "주식" 관련 핫 뉴스라고 가정
    # 필요하면 "증권", "코스피" 등 키워드 조합해서 확장 가능
    return await fetch_news("주식", limit=limit, sort=naver_sort)


# 🔹 2) 검색용 뉴스 엔드포인트
@app.get("/news")
async def search_news(
    keyword: str = Query(..., description="종목명 또는 키워드 (예: 삼성전자, AI, 반도체, 삼성 AI 등)"),
    limit: int = Query(30, ge=1, le=100),
    sort: str = Query("latest", description="latest | popular")
):
    """
    검색창에서 사용하는 엔드포인트
    - keyword: 자유 검색 (종목명, 키워드, 자연어 다 가능)
    - sort:
        latest  -> 최신순 (date)
        popular -> 관련도/인기순 느낌 (sim)
    """
    if sort in ["latest", "date", "time"]:
        naver_sort = "date"
    else:  # popular
        naver_sort = "sim"

    return await fetch_news(keyword, limit=limit, sort=naver_sort)


# 🔹 3) 기사 상세 보기 (본문 + 링크)
@app.get("/article")
async def get_article(url: str = Query(..., description="네이버 뉴스 기사 URL")):
    """
    뉴스 리스트에서 제목 클릭했을 때
    - 기사 본문 텍스트
    - 제목
    - 원문 링크
    를 반환하는 엔드포인트
    """
    return await fetch_article_content(url)
