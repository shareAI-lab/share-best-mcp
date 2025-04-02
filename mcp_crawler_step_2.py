import os
import json
import time
import aiohttp
import asyncio
from datetime import datetime
import base64
import config

# 配置
GITHUB_TOKEN = config.GITHUB_TOKEN
INPUT_FILE = "mcp_basic_repos.jsonl"
OUTPUT_FILE = "mcp_full_repos.jsonl"
FAILED_REPOS_FILE = "mcp_failed_repos.jsonl"
MAX_CONCURRENT = 3  # 最大并发数
RETRY_DELAY = 60    # 触发限制时的等待时间(秒)
REQUEST_DELAY = 2   # 请求间隔(秒)

async def log(message):
    """输出带时间戳的日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def load_processed_repos() -> set:
    """加载已处理的仓库列表"""
    processed = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    repo_data = json.loads(line.strip())
                    processed.add(repo_data["full_name"])
                except:
                    continue
    return processed

def load_failed_repos() -> set:
    """加载已知失败的仓库列表"""
    failed = set()
    if os.path.exists(FAILED_REPOS_FILE):
        with open(FAILED_REPOS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    repo_data = json.loads(line.strip())
                    failed.add(repo_data["full_name"])
                except:
                    continue
    return failed

def load_and_deduplicate_repos(input_file: str) -> list:
    """加载并去重第一阶段的仓库数据"""
    seen_repos = set()
    unique_repos = []
    
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                repo_data = json.loads(line.strip())
                full_name = repo_data["full_name"]
                if full_name not in seen_repos:
                    seen_repos.add(full_name)
                    unique_repos.append(repo_data)
            except json.JSONDecodeError:
                continue
    
    return unique_repos

async def record_failed_repo(repo_data: dict, error_msg: str):
    """记录失败的仓库信息"""
    failed_data = {
        "full_name": repo_data["full_name"],
        "error_message": error_msg,
        "failed_at": datetime.now().isoformat()
    }
    async with asyncio.Lock():
        with open(FAILED_REPOS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(failed_data, ensure_ascii=False) + "\n")

async def get_repo_readme(session, repo_full_name, semaphore, max_retries=3):
    """获取仓库的README内容，支持重试"""
    async with semaphore:
        retries = 0
        last_error = None
        
        while retries <= max_retries:
            try:
                await asyncio.sleep(REQUEST_DELAY)
                
                readme_url = f"https://api.github.com/repos/{repo_full_name}/readme"
                headers = {"Authorization": f"token {GITHUB_TOKEN}"}
                
                async with session.get(readme_url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = base64.b64decode(data["content"]).decode("utf-8")
                        return {
                            "content": content,
                            "size": len(content),
                            "path": data.get("path", ""),
                            "sha": data.get("sha", "")
                        }
                    elif resp.status == 403:  # Rate limit exceeded
                        reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                        wait_time = max(reset_time - time.time(), 0) + 5
                        await log(f"达到API限制，等待 {wait_time} 秒...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    last_error = f"HTTP状态码: {resp.status}"
                    if retries < max_retries:
                        retries += 1
                        await log(f"获取README失败: {repo_full_name}, {last_error}, 第{retries}次重试...")
                        await asyncio.sleep(REQUEST_DELAY * (retries + 1))
                        continue
            except Exception as e:
                last_error = str(e)
                if retries < max_retries:
                    retries += 1
                    await log(f"获取README出错: {repo_full_name}, 错误: {last_error}, 第{retries}次重试...")
                    await asyncio.sleep(REQUEST_DELAY * (retries + 1))
                    continue
        
        return {"error": f"获取README最终失败: {last_error}"}

async def process_repo(repo_data, session, semaphore, processed_repos):
    """处理单个仓库，添加README信息"""
    try:
        if repo_data["full_name"] in processed_repos:
            await log(f"跳过已处理的仓库: {repo_data['full_name']}")
            return

        readme_data = await get_repo_readme(session, repo_data["full_name"], semaphore)
        
        if readme_data and "error" in readme_data:
            await record_failed_repo(repo_data, readme_data["error"])
            return

        repo_data["readme"] = readme_data
        repo_data["readme_updated_at"] = datetime.now().isoformat()
        
        async with asyncio.Lock():
            with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(repo_data, ensure_ascii=False) + "\n")
            processed_repos.add(repo_data["full_name"])
            
        await log(f"已更新README信息: {repo_data['full_name']}")
        
    except Exception as e:
        error_msg = f"处理仓库时发生错误: {str(e)}"
        await log(f"{error_msg}: {repo_data['full_name']}")
        await record_failed_repo(repo_data, error_msg)

async def main():
    await log("开始获取仓库README信息...")
    
    # 创建信号量控制并发
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    # 加载已处理和失败的仓库
    processed_repos = load_processed_repos()
    failed_repos = load_failed_repos()
    await log(f"已处理的仓库数量: {len(processed_repos)}")
    await log(f"失败的仓库数量: {len(failed_repos)}")
    
    # 读取并去重第一步的基础信息
    repos = load_and_deduplicate_repos(INPUT_FILE)
    # 过滤掉已知失败的仓库
    repos = [repo for repo in repos if repo["full_name"] not in failed_repos]
    await log(f"加载了 {len(repos)} 个待处理的仓库基础信息")
    
    async with aiohttp.ClientSession() as session:
        tasks = [
            process_repo(repo, session, semaphore, processed_repos)
            for repo in repos
        ]
        
        async def show_progress():
            total = len(tasks)
            while True:
                completed = len(processed_repos)
                await log(f"进度: {completed}/{total} ({completed/total*100:.1f}%)")
                if completed >= total:
                    break
                await asyncio.sleep(10)
        
        progress_task = asyncio.create_task(show_progress())
        await asyncio.gather(*tasks)
        await progress_task
    
    await log("README信息获取完成！")

if __name__ == "__main__":
    asyncio.run(main())