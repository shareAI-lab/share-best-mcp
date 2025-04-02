import os
import json
import time
import aiohttp
import asyncio
from datetime import datetime
from urllib.parse import quote
import config

# 配置
GITHUB_TOKEN = config.GITHUB_TOKEN
OUTPUT_FILE = "mcp_basic_repos.jsonl"
START_DATE = "2024-10-01"

# 搜索关键词列表
SEARCH_QUERIES = [
    "mcp+server in:name",
    # "mcp in:name",
    "mcp+server in:description",
    "model+context+protocol in:description",
    "mcp+server in:readme",
    "claude-mcp",
    "anthropic-mcp",
    "openai-mcp"
]

async def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

async def save_repo(repo):
    """只保存基础仓库信息到JSONL文件"""
    try:
        repo_data = {
            "id": repo["id"],
            "name": repo["name"],
            "full_name": repo["full_name"],
            "html_url": repo["html_url"],
            "description": repo["description"],
            "owner": repo["owner"]["login"],
            "created_at": repo["created_at"],
            "updated_at": repo["updated_at"],
            "language": repo["language"],
            "stargazers_count": repo["stargazers_count"],
            "forks_count": repo["forks_count"],
            "topics": repo.get("topics", []),
            "crawled_at": datetime.now().isoformat()
        }
        
        # 直接写入基础信息，不获取README
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(repo_data, ensure_ascii=False) + "\n")
            
        await log(f"已保存基础信息: {repo['full_name']}")
        
    except Exception as e:
        await log(f"保存仓库 {repo['full_name']} 时出错: {str(e)}")

async def search_repositories(session, query, page=1):
    """搜索GitHub仓库"""
    try:
        search_url = (
            "https://api.github.com/search/repositories"
            f"?q={quote(query)}+created:>{START_DATE}"
            f"&sort=updated&page={page}&per_page=100"
        )
        
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        async with session.get(search_url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("items", [])
            elif response.status == 403:
                # Rate limit exceeded
                reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
                sleep_time = max(reset_time - time.time(), 0) + 10
                await log(f"达到API限制，等待 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)
                return []
            else:
                await log(f"搜索请求失败: {response}")
                return []
                
    except Exception as e:
        await log(f"搜索仓库时出错: {str(e)}")
        return []

async def main():
    await log("开始爬取MCP相关仓库...")
    
    # 创建新的JSONL文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        pass
    
    async with aiohttp.ClientSession() as session:
        for query in SEARCH_QUERIES:
            page = 1
            while True:
                await log(f"搜索: {query}, 页码: {page}")
                repos = await search_repositories(session, query, page)
                
                if not repos:
                    break
                
                # 并发保存仓库信息
                tasks = [save_repo(repo) for repo in repos]
                await asyncio.gather(*tasks)
                
                if len(repos) < 100:  # 最后一页
                    break
                    
                page += 1
                await asyncio.sleep(1)  # 避免触发API限制
    
    await log("爬取完成！")

if __name__ == "__main__":
    asyncio.run(main()) 
    