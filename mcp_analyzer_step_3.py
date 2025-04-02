import os
import json
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Set
from xml.etree import ElementTree
from config import ( 
    MODEL_CONFIGS,
    CURRENT_MODEL,
    MAX_CONCURRENT,
    MAX_RETRIES,
    RETRY_DELAY
)
from enum import Enum
import sys
import traceback

# 配置
INPUT_FILE = "mcp_full_repos.jsonl"
OUTPUT_FILE = "mcp_analysis_results.json"

# 添加新的常量定义
RESULTS_DIR = "results"
PROCESSED_URLS_FILE = f"{RESULTS_DIR}/processed_urls.txt"
LOG_FILE = f"{RESULTS_DIR}/analysis.log"

class Color(Enum):
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    END = '\033[0m'

# 修改日志函数
async def log(message, level="INFO", write_to_file=True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 只有ERROR级别使用红色输出到终端
    if level == "ERROR":
        console_message = f"{Color.RED.value}[{timestamp}] [{level}] {message}{Color.END.value}"
        print(console_message)
    else:
        # 非错误信息使用普通输出
        print(f"[{timestamp}] [{level}] {message}")
    
    # 文件写入不带颜色
    if write_to_file:
        log_message = f"[{timestamp}] [{level}] {message}"
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")

def load_and_deduplicate_repos() -> List[Dict]:
    """加载并去重仓库数据，过滤掉无效数据"""
    repos = []
    seen_urls: Set[str] = set()
    skipped_count = {
        "no_readme": 0,
        "duplicate": 0,
        "processed": 0
    }
    
    # 获取所有已处理的仓库
    processed_repos = set()
    for result_type in ["server", "tool", "index", "client", "other", "non_mcp", "error"]:
        filename = f"{RESULTS_DIR}/mcp_{result_type}.jsonl"
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        result = json.loads(line.strip())
                        processed_repos.add(result["repo_name"])
                    except (json.JSONDecodeError, KeyError):
                        continue
    
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                repo = json.loads(line.strip())
                url = repo["html_url"]
                
                # 检查是否已经处理过
                if repo["full_name"] in processed_repos:
                    skipped_count["processed"] += 1
                    continue
                
                # 检查是否重复
                if url in seen_urls:
                    skipped_count["duplicate"] += 1
                    continue
                
                # 检查是否有README内容
                readme = repo.get("readme", {})
                if not readme or not readme.get("content"):
                    skipped_count["no_readme"] += 1
                    continue
                
                # 通过所有检查，添加到结果列表
                seen_urls.add(url)
                repos.append(repo)
                
            except json.JSONDecodeError:
                continue
    
    # 使用同步方式输出统计信息
    print(f"\n仓库加载统计:")
    print(f"- 有效仓库数量: {len(repos)}")
    print(f"- 重复仓库数量: {skipped_count['duplicate']}")
    print(f"- 无README仓库数量: {skipped_count['no_readme']}")
    print(f"- 已处理仓库数量: {skipped_count['processed']}")
    print() # 添加空行使输出更清晰
    
    return repos

def get_processed_urls() -> Set[str]:
    """获取已处理过的URL列表"""
    if not os.path.exists(PROCESSED_URLS_FILE):
        return set()
    with open(PROCESSED_URLS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def mark_url_as_processed(url: str):
    """标记URL为已处理"""
    os.makedirs(os.path.dirname(PROCESSED_URLS_FILE), exist_ok=True)
    with open(PROCESSED_URLS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

async def save_result_to_jsonl(result: Dict, result_type: str):
    """保存单个结果到对应的JSONL文件"""
    filename = f"{RESULTS_DIR}/mcp_{result_type}.jsonl"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    await log(f"已保存结果到 {filename}")

async def validate_server_command(command_str: str) -> bool:
    """验证server_command是否为有效的JSON格式"""
    try:
        # 移除可能的多余空格和换行
        command_str = command_str.strip()
        # 解析JSON
        command_json = json.loads(command_str)
        # 验证基本结构
        if not isinstance(command_json, dict):
            return False
        if "mcpServers" not in command_json:
            return False
        if not isinstance(command_json["mcpServers"], dict):
            return False
        # 验证每个服务器配置
        for server_name, server_config in command_json["mcpServers"].items():
            if not isinstance(server_config, dict):
                return False
            if "command" not in server_config:
                return False
            if "args" in server_config and not isinstance(server_config["args"], list):
                return False
            if "env" in server_config and not isinstance(server_config["env"], dict):
                return False
        return True
    except json.JSONDecodeError as e:
        await log(f"server_command JSON解析失败: {str(e)}", level="ERROR")
        return False
    except Exception as e:
        await log(f"server_command验证时发生错误: {str(e)}", level="ERROR")
        return False

async def xml_to_json(xml_str: str) -> Dict:
    """将XML分析结果转换为JSON格式"""
    try:
        # 清理和验证XML字符串
        xml_str = xml_str.strip()
        
        # 处理可能的XML转义字符
        xml_str = xml_str.replace('&nbsp;', ' ')
        xml_str = xml_str.replace('&amp;', '&')
        xml_str = xml_str.replace('&lt;', '<')
        xml_str = xml_str.replace('&gt;', '>')
        
        if not xml_str.startswith('<mcp_response'):
            # 尝试提取XML部分
            start = xml_str.find('<mcp_response')
            end = xml_str.rfind('</mcp_response>') + len('</mcp_response>')
            if start >= 0 and end > start:
                xml_str = xml_str[start:end]
            else:
                # 如果无法找到有效的XML，返回错误结果
                await log("无法找到有效的mcp_response标签，返回错误结果", level="ERROR")
                return {
                    "is_mcp_related": False,
                    "reason": "XML解析失败：无法找到有效的mcp_response标签",
                    "score": 0
                }
        
        try:
            # 尝试解析XML
            root = ElementTree.fromstring(xml_str)
        except ElementTree.ParseError as e:
            # XML解析失败时的错误处理
            error_msg = f"""XML解析错误: {str(e)}
原始XML内容:
{xml_str}
错误位置: {e.position}"""
            await log(error_msg, level="ERROR")
            return {
                "is_mcp_related": False,
                "reason": f"XML解析失败：{str(e)}",
                "score": 0
            }
        
        # 基本字段
        is_mcp_related = root.find("is_mcp_related")
        if is_mcp_related is None:
            raise ValueError("缺少必需的is_mcp_related字段")
        result["is_mcp_related"] = is_mcp_related.text.lower() == "true"
        
        if not result["is_mcp_related"]:
            reason = root.find("reason")
            result["reason"] = reason.text if reason is not None else "未提供原因"
            # 添加评分解析
            score = root.find("score")
            if score is not None:
                try:
                    result["score"] = int(score.text.split(',')[0].strip())
                except (ValueError, TypeError):
                    await log("评分解析失败，设置为0", level="ERROR")
                    result["score"] = 0
            else:
                result["score"] = 0
            return result
            
        # MCP相关字段
        result["is_mcp_server"] = root.find("is_mcp_server").text.lower() == "true"
        result["repo_type"] = root.find("repo_type").text
        result["description"] = root.find("description").text
        
        if not result["is_mcp_server"]:
            result["reason"] = root.find("reason").text
            # 添加评分解析
            score = root.find("score")
            if score is not None:
                try:
                    result["score"] = int(score.text.split(',')[0].strip())
                except (ValueError, TypeError):
                    await log("评分解析失败，设置为0", level="ERROR")
                    result["score"] = 0
            else:
                result["score"] = 0
            return result
        
        # MCP服务器特有字段
        server_fields = [
            "name", "author", "url", "readme", "github_username",
            "server_name", "is_command_guessed", "server_command",
            "is_stateless", "stateless_reason", "deployment_mode",
            "deployment_reason", "is_easy_install"  # 添加is_easy_install到服务器字段列表
        ]
        
        for field in server_fields:
            elem = root.find(field)
            if elem is not None:
                if field == "server_command":
                    # 解析JSON字符串
                    try:
                        result[field] = json.loads(elem.text)
                    except json.JSONDecodeError:
                        result[field] = elem.text  # 保留原始文本
                elif field in ["is_command_guessed", "is_stateless", "is_easy_install"]:  # 添加is_easy_install到布尔字段列表
                    result[field] = elem.text.lower() == "true"
                else:
                    result[field] = elem.text
        
        # 处理参数列表
        params = root.find("params")
        if params is not None:
            result["params"] = [param.text for param in params.findall("param")]
        
        # 处理标签
        tags = root.find("tags")
        if tags is not None:
            result["tags"] = [tag.text for tag in tags.findall("tag")]
        
        # 添加评分解析
        score = root.find("score")
        if score is not None:
            try:
                result["score"] = int(score.text.split(',')[0].strip())
            except (ValueError, TypeError):
                await log("评分解析失败，设置为0", level="ERROR")
                result["score"] = 0
        else:
            result["score"] = 0
        
        return result

    except Exception as e:
        error_msg = f"""XML处理错误: {str(e)}
原始XML内容:
{xml_str}
堆栈跟踪:
{traceback.format_exc()}"""
        await log(error_msg, level="ERROR")
        return {
            "is_mcp_related": False,
            "reason": f"XML处理错误：{str(e)}",
            "score": 0
        }

async def call_llm_api(session, prompt: str, retries: int = 0) -> Dict:
    """调用LLM API并支持重试"""
    try:
        # 获取当前模型配置
        if CURRENT_MODEL not in MODEL_CONFIGS:
            raise ValueError(f"未找到模型配置: {CURRENT_MODEL}")
        
        model_config = MODEL_CONFIGS[CURRENT_MODEL]
        await log(f"使用模型: {CURRENT_MODEL} ({model_config['id']})")
        
        headers = {
            "Authorization": f"Bearer {model_config['api_key']}",
            "Content-Type": "application/json"
        }
        
        # 添加超时设置
        timeout = aiohttp.ClientTimeout(total=60)  # 60秒超时
        
        async with session.post(
            model_config["url"],
            headers=headers,
            json={
                "model": model_config["id"],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                await log(f"API请求失败: HTTP {response.status}, 响应: {error_text}", level="ERROR")
                if retries < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * (retries + 1))  # 指数退避
                    return await call_llm_api(session, prompt, retries + 1)
                raise Exception(f"API调用失败，已重试{MAX_RETRIES}次")
            
            response_json = await response.json()
            
            # 验证响应格式
            if not isinstance(response_json, dict) or 'choices' not in response_json:
                raise ValueError(f"API响应格式错误: {response_json}")
                
            if not response_json['choices'] or 'message' not in response_json['choices'][0]:
                raise ValueError(f"API响应缺少必要字段: {response_json}")
                
            return response_json
            
    except asyncio.TimeoutError:
        await log(f"API请求超时 (重试次数: {retries}/{MAX_RETRIES})", level="ERROR")
        if retries < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY * (retries + 1))
            return await call_llm_api(session, prompt, retries + 1)
        raise
    except Exception as e:
        await log(f"API调用出错 (重试次数: {retries}/{MAX_RETRIES}): {str(e)}\n{traceback.format_exc()}", level="ERROR")
        if retries < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY * (retries + 1))
            return await call_llm_api(session, prompt, retries + 1)
        raise

async def generate_readme(session, repo: Dict) -> str:
    """生成详细的README文档"""
    prompt = f"""请根据以下仓库信息生成一篇详细的文档介绍：

仓库信息:
- 仓库名称: {repo['name']}
- 仓库作者: {repo['owner']}
- 仓库URL: {repo['html_url']}
- 仓库描述: {repo['description']}
- README内容: {repo.get('readme', {}).get('content', '')}

请生成一篇详细的关于仓库的中文文档介绍，要求：
1. 比原始资料更加详细生动、准确专业、文采流畅
2. 内容务必完整正确，如果原资料中没有提到某些重要信息就不要自己添加
3. 整个文章应该越长越好，包含其潜在的应用场景价值
4. 注意不要包含幻觉内容
5. 使用严格的Markdown格式
6. 务必使用中文

请使用以下XML格式返回：
<mcp_readme_response>
    <content><![CDATA[
在这里生成详细的Markdown格式文档
    ]]></content>
</mcp_readme_response>
"""
    
    try:
        response = await call_llm_api(session, prompt)
        xml_result = response["choices"][0]["message"]["content"]
        
        # 清理和验证XML字符串
        xml_result = xml_result.strip()
        if not xml_result.startswith('<mcp_readme_response'):
            # 尝试提取XML部分
            start = xml_result.find('<mcp_readme_response')
            end = xml_result.rfind('</mcp_readme_response>') + len('</mcp_readme_response>')
            if start >= 0 and end > start:
                xml_result = xml_result[start:end]
            else:
                await log("无法找到有效的mcp_readme_response标签", level="ERROR")
                return None

        try:
            root = ElementTree.fromstring(xml_result)
            content = root.find('content')
            if content is not None:
                readme_text = content.text.strip()
                if readme_text:
                    return readme_text
                
            await log("README内容为空", level="ERROR")
            return None
            
        except ElementTree.ParseError as e:
            await log(f"README XML解析失败: {str(e)}\n原始内容: {xml_result}", level="ERROR")
            return None
            
    except Exception as e:
        await log(f"生成README失败: {str(e)}", level="ERROR")
        return None

async def analyze_easy_install(session, server_command: Dict) -> bool:
    """分析服务器命令是否容易安装"""
    prompt = f"""请分析以下MCP服务器的启动命令，判断它是否容易安装和部署。

服务器命令配置:
{json.dumps(server_command, indent=2, ensure_ascii=False)}

判断标准:
1. 如果command主程序不是npx/uvx/docker这3个中的一个，就为false
2. 如果程序运行需要指定具体的本地路径，就为false
3. 如果需要先手动下载仓库代码，就为false
4. 如果运行参数args或环境变量env中需要用户自行指定本地文件path，就为false
5. 只有使用标准包管理器或容器化部署，且无需额外本地文件配置的才为true

请严格按照以下XML格式返回，不要添加任何其他内容：
<easy_install_response>
    <is_easy_install>true/false</is_easy_install>
    <reason>详细解释为什么容易/不容易安装</reason>
</easy_install_response>
"""

    try:
        response = await call_llm_api(session, prompt)
        xml_result = response["choices"][0]["message"]["content"]
        
        # 清理和验证XML字符串
        xml_result = xml_result.strip()
        if not xml_result.startswith('<easy_install_response'):
            # 尝试提取XML部分
            start = xml_result.find('<easy_install_response')
            end = xml_result.rfind('</easy_install_response>') + len('</easy_install_response>')
            if start >= 0 and end > start:
                xml_result = xml_result[start:end]
            else:
                await log(f"无法找到有效的easy_install_response标签，原始响应：\n{xml_result}", level="ERROR")
                return False

        # 确保XML格式正确
        xml_result = xml_result.replace('&', '&amp;')  # 转义特殊字符
        
        try:
            root = ElementTree.fromstring(xml_result)
            is_easy_install = root.find('is_easy_install')
            reason = root.find('reason')
            
            if is_easy_install is not None:
                result = is_easy_install.text.lower() == 'true'
                await log(f"Easy install 分析结果: {result}, 原因: {reason.text if reason is not None else '未提供'}")
                return result
            else:
                await log(f"无法找到is_easy_install标签，XML内容：\n{xml_result}", level="ERROR")
                return False
                
        except ElementTree.ParseError as e:
            error_msg = f"""Easy install XML解析失败: {str(e)}
原始XML内容:
{xml_result}
错误位置: {getattr(e, 'position', 'unknown')}"""
            await log(error_msg, level="ERROR")
            return False
            
    except Exception as e:
        await log(f"分析easy install失败: {str(e)}\n{traceback.format_exc()}", level="ERROR")
        return False

async def analyze_repo(session, repo: Dict, semaphore: asyncio.Semaphore):
    """分析单个仓库"""
    async with semaphore:
        try:
            await log(f"开始分析仓库: {repo['full_name']}")
            
            if repo['html_url'] in get_processed_urls():
                await log(f"仓库 {repo['full_name']} 已处理过，跳过")
                return None

            # 添加重试计数器
            retry_count = 0
            max_retries = 3
            
            while retry_count < max_retries:
                try:
                    # 首先生成README
                    readme_content = await generate_readme(session, repo)
                    
                    # 主要分析
                    prompt = f"""请详细分析这个GitHub仓库是否与MCP（模型上下文协议，Model Context Protocol）相关，并确定它的具体类型。

详细仓库信息:
- 仓库名称: {repo['name']}
- 仓库作者: {repo['owner']}
- 作者用户名: {repo['owner']}
- 仓库URL: {repo['html_url']}
- 仓库描述: {repo['description']}
- 创建时间: {repo['created_at']}
- 最后更新: {repo['updated_at']}
- Stars数量: {repo['stargazers_count']}
- Forks数量: {repo['forks_count']}
- 主要语言: {repo.get('language', '未知')}

仓库README内容:
{repo.get('readme', {}).get('content', '')}

MCP相关仓库可能属于以下几种类型:
1. MCP服务器(Server): 提供具体功能的MCP服务器实现，可由大语言模型直接调用的服务
2. MCP导航/索引(Index): 收集、列出或汇总各种MCP服务器的资源列表（如awesome-mcp）
3. MCP开发工具(Tool): 用于开发MCP服务器的工具、框架、SDK或库
4. MCP客户端(Client): 用于连接MCP服务器的客户端实现
5. 其他MCP相关(Other): 与MCP相关但不属于上述类别的仓库
6. 非MCP相关(NotMCP): 与MCP无关的仓库

请首先判断这个仓库是否与MCP相关，然后确定它属于哪种具体类型。

如果仓库与MCP无关，请返回以下XML格式:
<mcp_response>
  <is_mcp_related>false</is_mcp_related>
  <reason>解释为什么这个仓库与MCP无关</reason>
  <score>0-100，通过仓库内容分析仓库质量给仓库打分，只需给出分数不要给出任何解释，分数越高说明质量越好</score>
</mcp_response>

如果仓库与MCP相关但不是MCP服务器，请返回以下XML格式:
<mcp_response>
  <is_mcp_related>true</is_mcp_related>
  <is_mcp_server>false</is_mcp_server>
  <reason>详细解释为什么这不是MCP服务器，而是另一种类型</reason>
  <repo_type>Index/Tool/Client/Other</repo_type>
  <name>{repo['name']}</name>
  <author>{repo['owner']}</author>
  <description>60字左右的简洁中文描述，概括此MCP服务器的功能和价值</description>
  <url>{repo['html_url']}</url>
  <tags>
    <tag>标签1</tag>
    <tag>标签2</tag>
    <tag>标签3</tag>
  </tags>
  <score>0-100，通过仓库内容分析仓库质量给仓库打分，只需给出分数不要给出任何解释，分数越高说明质量越好</score>
</mcp_response>

如果仓库是MCP服务器，请提供以下详细信息，使用XML格式返回:
<mcp_response>
  <is_mcp_related>true</is_mcp_related>
  <is_mcp_server>true</is_mcp_server>
  <repo_type>Server</repo_type>
  <name>{repo['name']}</name>
  <author>{repo['owner']}</author>
  <description>60字左右的简洁中文描述，概括此MCP服务器的功能和价值</description>
  <url>{repo['html_url']}</url>
  <github_username>{repo['owner']}</github_username>
  <server_name>@{repo['owner']}/{repo['name']}</server_name>
  <server_command>
{{
  "mcpServers": {{
    "{repo['owner']}-{repo['name']}": {{
      "command": "启动命令的主程序 (确保根据仓库资料严谨推测，一般是一个专用的二进制程序)",
      "args": [
        "参数1",
        "参数2",
        ... 这里是程序启动的命令必要参数，不要含有中文或随机提示内容，务必确保拼接后能正确启动服务，不要添加任何多余参数
        忽略资料中任何与sse的host或port指定相关的参数
        如果是docker启动，不要通过--env-file指定环境变量文件，而是直接在args中直接指定需要的环境变量, 每个环境变量都需要单独的 -e 参数，如：-e ENV_VAR_1 -e ENV_VAR_2,如：docker run -i --rm -e ENV_VAR_1 -e ENV_VAR_2 mcp/github
        涉及到这种自定义变量的，args 数组中只写变量名，实际的变量值放在 env 对象中
      ],
      "env": {{
        "ENV_VAR_1": "值1",
        ... 这里是作为额外信息参数，如需要用户自定义KEY、URL等，严格根据仓库资料推测，如果仓库中没有提供就没有，不要自己添加任何多余参数
      }}
    }}
  }}
}}
  </server_command>
  <params>
    <param>ENV_VAR_1</param>
    ... 这里与上面env中的参数列表名称严格一一对应，如果仓库中没有提供就没有，不要添加任何多余参数
  </params>
  <is_command_guessed>true/false</is_command_guessed>
  <is_stateless>true/false</is_stateless>
  <stateless_reason>详细解释为什么这是/不是无状态服务</stateless_reason>
  <deployment_mode>cloud/local/both</deployment_mode>
  <deployment_reason>详细解释为什么这个MCP服务器更适合云端部署/本地部署/两者都适合</deployment_reason>
  <tags>
    <tag>标签1</tag>
    <tag>标签2</tag>
    ... 标签数量3~5个，根据实际情况提供，优先使用中文标签
  </tags>
  <score>0-100，通过仓库内容分析仓库质量给仓库打分，只需给出分数不要给出任何解释，分数越高说明质量越好</score>
</mcp_response>
请务必仔细阅读仓库资料，确保准确无误，不要遗漏任何重要信息，不要添加任何多余信息，不要给出错误的格式信息

注意事项:
1. 请基于仓库内容给出准确的判断，特别是区分真正的MCP服务器与其他类型
2. 特别注意识别"awesome-xxx"类型的集合/索引仓库，这些不是MCP服务器
3. 如果是开发工具、SDK或框架，请标记为Tool类型而非Server
4. is_stateless字段标识服务是否无状态，true表示无状态,易于水平扩展，false表示有状态，请根据仓库内容判断
5. is_command_guessed标签必须填写，true表示server_command是猜测的，false表示是从仓库中直接获取的
6. server_command中的参数数量不固定，根据实际情况提供，命令应该尽可能准确，格式与给定json结构严格一致 
示例：
    { {"mcpServers":{"github":{"command":"docker","args":["run","-i","--rm","-e","GITHUB_PERSONAL_ACCESS_TOKEN","mcp/github"],"env":{"GITHUB_PERSONAL_ACCESS_TOKEN":"<YOUR_TOKEN>"}}}} }
    { {"mcpServers":{"matlab":{"command":"uv","args":["--directory","/absolute/path/to/matlab-mcp","run","matlab_server.py"],"env":{"MATLAB_PATH":"/Applications/MATLAB_R2024a.app"}}}} }
7. deployment_mode必须是cloud(更适合云端部署)、local(更适合本地部署)或both(两者都适合)三者之一
8. 判断deployment_mode时，考虑服务的性能需求、资源消耗、隐私问题和用途等因素
9. 如果仓库中没有提供启动命令，请根据仓库内容推测一个合理的命令，并将<is_command_guessed>设为true
10. 关于服务器状态(is_stateless)的判断标准：
   - 无状态(true)的特征：
     * 易于水平扩展
     * 每个请求都是独立的，不依赖之前的请求状态
     * 不需要持久化存储数据
     * 不维护会话信息
     * 可以随时重启而不影响服务质量
   - 有状态(false)的特征：
     * 扩展需要考虑数据同步
     * 需要保存用户会话或上下文信息
     * 依赖数据库或文件系统存储
     * 需要维护长连接
     * 重启可能导致进行中的操作中断

11. 关于命令推测(is_command_guessed)的判断：
   - 确定来源(false)的情况：
     * README中明确提供了启动命令
     * 代码仓库中有明确的启动脚本
     * 有详细的部署文档说明
   - 推测得出(true)的情况：
     * 根据项目结构推测的启动方式
     * 基于常见框架默认启动方式推测
     * 没有明确的启动说明文档

12. 仓库类型判断的优先级：
   - 首先确认是否与MCP真正相关
   - 特别注意识别"awesome-xxx"类型的导航/索引仓库
   - 区分开发工具/SDK与实际服务器
   - 客户端实现单独归类
   - 有疑虑时优先归类为Other而非Server

13. 分析结果可信度要求：
   - 不确定的配置项宁可空缺也不要随意填写
   - 保持分析结果的一致性和可验证性

"""

                    # 调用API进行主要分析
                    api_response = await call_llm_api(session, prompt)
                    xml_result = api_response["choices"][0]["message"]["content"]
                    
                    # 验证XML结果
                    if not xml_result or not xml_result.strip():
                        raise ValueError("API返回的XML结果为空")
                    
                    await log(f"API原始响应:\n{xml_result}", level="DEBUG")
                    
                    # 将XML转换为JSON
                    json_result = await xml_to_json(xml_result)
                    
                    if not json_result:
                        raise ValueError("XML转JSON结果为空")
                    
                    # 如果是MCP服务器，分析easy install
                    if json_result.get("is_mcp_related") and json_result.get("is_mcp_server"):
                        server_command = json_result.get("server_command")
                        if server_command:
                            await log(f"开始分析 easy install，server_command: {json.dumps(server_command, ensure_ascii=False)}")
                            is_easy_install = await analyze_easy_install(session, server_command)
                            
                            # 直接在json_result中设置结果
                            json_result["is_easy_install"] = is_easy_install
                            
                            # 更新XML以保持一致性
                            xml_content = xml_result.strip()
                            insert_pos = xml_content.rfind('</mcp_response>')
                            if insert_pos > 0:
                                # 先检查是否已存在is_easy_install标签
                                if '<is_easy_install>' not in xml_content:
                                    full_xml = (
                                        f"{xml_content[:insert_pos]}"
                                        f"  <is_easy_install>{str(is_easy_install).lower()}</is_easy_install>\n"
                                        f"{xml_content[insert_pos:]}"
                                    )
                                    xml_result = full_xml
                        else:
                            await log("未找到server_command配置，设置is_easy_install为false", level="WARN")
                            json_result["is_easy_install"] = False
                    
                    # 如果是MCP相关且README生成成功，添加README内容
                    if json_result.get("is_mcp_related") and readme_content is not None:
                        xml_content = xml_result.strip()
                        insert_pos = xml_content.rfind('</mcp_response>')
                        if insert_pos > 0:
                            full_xml = (
                                f"{xml_content[:insert_pos]}"
                                f"  <readme><![CDATA[{readme_content}]]></readme>\n"
                                f"{xml_content[insert_pos:]}"
                            )
                            # 重新解析完整的XML
                            json_result = await xml_to_json(full_xml)
                    
                    result = {
                        "repo_name": repo["full_name"],
                        "analysis_time": datetime.now().isoformat(),
                        "analysis": json_result
                    }

                    # 根据类型保存结果
                    if not json_result["is_mcp_related"]:
                        await save_result_to_jsonl(result, "non_mcp")
                    else:
                        repo_type = json_result["repo_type"].lower()
                        await save_result_to_jsonl(result, repo_type)

                    # 标记为已处理
                    mark_url_as_processed(repo['html_url'])
                    await log(f"成功完成仓库 {repo['full_name']} 的分析")
                    
                    return result

                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        raise
                    await log(f"重试第 {retry_count} 次: {str(e)}", level="WARN")
                    await asyncio.sleep(RETRY_DELAY * retry_count)  # 指数退避
                    
        except Exception as e:
            error_msg = f"""分析仓库失败: {repo['full_name']}
错误类型: {type(e).__name__}
错误信息: {str(e)}
堆栈跟踪:
{traceback.format_exc()}"""
            await log(error_msg, level="ERROR")
            error_result = {
                "repo_name": repo["full_name"],
                "analysis_time": datetime.now().isoformat(),
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc()
            }
            await save_result_to_jsonl(error_result, "error")
            return None

async def main():
    await log("开始MCP仓库分析...")
    
    # 创建结果目录
    os.makedirs(RESULTS_DIR, exist_ok=True)
    
    # 加载并去重仓库
    repos = load_and_deduplicate_repos()
    
    if not repos:
        await log("没有需要处理的仓库，程序退出", level="WARN")
        return
        
    await log(f"加载了 {len(repos)} 个待处理仓库")
    
    # 创建信号量限制并发
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    async with aiohttp.ClientSession() as session:
        tasks = [
            analyze_repo(session, repo, semaphore)
            for repo in repos
        ]
        
        # 显示进度的协程
        async def show_progress():
            total = len(tasks)
            completed = 0
            while completed < total:
                completed = sum(1 for t in tasks if t.done())
                await log(f"进度: {completed}/{total} ({completed/total*100:.1f}%)")
                await asyncio.sleep(5)
        
        # 启动进度显示
        progress_task = asyncio.create_task(show_progress())
        
        # 等待所有分析任务完成
        await asyncio.gather(*tasks)
        
        # 取消进度显示
        progress_task.cancel()
    
    await log("分析完成！")

if __name__ == "__main__":
    asyncio.run(main())