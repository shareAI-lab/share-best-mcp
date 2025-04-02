import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Set
import os

# 常量定义
INPUT_DIR = "results"
INPUT_FILE = f"{INPUT_DIR}/mcp_server.jsonl"
OUTPUT_FILE = "mcp_server_filtered.jsonl"
AWESOME_FILE = "awesome-mcp-servers.md"
MIN_SCORE = 85
MIN_CLUSTER_SIZE = 3  # 每个分类最少需要3个项目

def load_mcp_servers() -> List[Dict]:
    """加载所有MCP服务器数据"""
    servers = []
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    server = json.loads(line.strip())
                    if (server['analysis']['is_mcp_related'] and 
                        server['analysis']['is_mcp_server'] and 
                        server['analysis']['score'] >= MIN_SCORE):
                        servers.append(server)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        print(f"未找到输入文件: {INPUT_FILE}")
        return []
        
    return servers

def save_filtered_servers(servers: List[Dict]):
    """保存过滤后的服务器列表"""
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for server in servers:
            json.dump(server, f, ensure_ascii=False)
            f.write('\n')
    print(f"已保存过滤后的服务器列表到: {OUTPUT_FILE}")

def should_filter_tag(tag: str) -> bool:
    """检查标签是否应该被过滤（包含mcp关键词）"""
    return 'mcp' in tag.lower()

def get_tag_clusters(servers: List[Dict]) -> Dict[str, List[Dict]]:
    """按标签聚类服务器,忽略包含MCP的标签,并将小分类归入其他"""
    # 第一步：创建初始分类
    initial_clusters = defaultdict(list)
    
    # 记录每个服务器是否已被分类
    server_classified = {server['analysis']['name']: False for server in servers}
    
    # 首先按标签分类
    for server in servers:
        tags = set(server['analysis'].get('tags', []))
        # 过滤掉包含mcp的标签（不区分大小写）
        valid_tags = {tag for tag in tags if not should_filter_tag(tag)}
        
        # 为每个有效标签添加服务器
        for tag in valid_tags:
            initial_clusters[tag].append(server)
    
    # 第二步：整理分类
    final_clusters = {}
    others = []  # 存放未能归类的服务器
    
    # 先处理满足最小数量要求的分类
    for tag, cluster_servers in initial_clusters.items():
        if len(cluster_servers) >= MIN_CLUSTER_SIZE:
            final_clusters[tag] = cluster_servers
            # 标记这些服务器已被分类
            for server in cluster_servers:
                server_classified[server['analysis']['name']] = True
    
    # 将未分类的服务器归入"其他"类别
    for server in servers:
        if not server_classified[server['analysis']['name']]:
            others.append(server)
    
    # 如果有未分类的服务器，添加"其他"类别
    if others:
        final_clusters["其他"] = others
    
    return final_clusters

def sanitize_anchor(text: str) -> str:
    """生成标准化的锚点ID"""
    # 将中文和其他特殊字符转换为横线
    # 1. 转换为小写
    # 2. 将空格、斜杠等转换为横线
    # 3. 移除其他特殊字符
    # 4. 确保符合 GitHub 的锚点规则
    return ''.join(
        char for char in text.lower()
        .replace(' ', '-')
        .replace('/', '-')
        .replace('(', '')
        .replace(')', '')
        if char.isalnum() or char == '-'
    )

def generate_awesome_md(clusters: Dict[str, List[Dict]]):
    """生成Awesome MCP Servers markdown文档"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(AWESOME_FILE, 'w', encoding='utf-8') as f:
        # 写入标题和介绍
        f.write(f"""# Share Best MCP Servers

精选的高质量MCP（Model Context Protocol）服务器列表，系统评分 >= {MIN_SCORE}。\n 这些服务器可以被大语言模型直接调用，提供各种强大的功能扩展。

> 🔄 最后更新: {current_time}

## 高质量MCP中文搜索引擎 
访问网址：[ShareMCP.cn](https://sharemcp.cn)

## 目录

""")
        
        # 生成目录，使用标准化的锚点ID
        for tag in sorted(clusters.keys()):
            anchor = sanitize_anchor(tag)
            server_count = len(clusters[tag])
            f.write(f"- [{tag}](#{anchor}) ({server_count})\n")
        
        f.write("\n---\n\n")
        
        # 按标签分类写入服务器信息
        first_category = True
        for tag in sorted(clusters.keys()):
            # 使用相同的锚点ID生成规则
            anchor = sanitize_anchor(tag)
            server_count = len(clusters[tag])
            # 使用 <h2 id="..."> 确保锚点正确
            f.write(f'<h2 id="{anchor}">{tag} ({server_count})</h2>\n\n')
            
            # 对每个标签下的服务器按分数降序排序
            servers = sorted(clusters[tag], 
                           key=lambda x: x['analysis']['score'], 
                           reverse=True)
            
            # 使用details标签实现可折叠效果，第一个分类默认展开
            f.write(f"<details {'open' if first_category else ''}>\n<summary>点击展开/折叠</summary>\n\n")
            
            for server in servers:
                analysis = server['analysis']
                
                # 使用表格形式展示基本信息
                f.write(f"### [{analysis['name']}]({analysis['url']})\n\n")
                f.write(f"{analysis['description']}\n\n")
                
                f.write("| 属性 | 值 |\n")
                f.write("| --- | --- |\n")
                f.write(f"| 作者 | {analysis['author']} |\n")
                f.write(f"| 评分 | {analysis['score']} |\n")
                f.write(f"| 部署模式 | {'本地' if analysis['deployment_mode'] == 'local' else '云端' if analysis['deployment_mode'] == 'cloud' else '本地、云端'} |\n")
                f.write(f"| 服务状态 | {'✅ 无状态' if analysis['is_stateless'] else '⚠️ 有状态'} |\n")
                f.write(f"| 易安装性 | {'✅ 易安装' if analysis.get('is_easy_install', False) else '⚠️ 需要配置'} |\n")
                
                # 标签使用badge样式
                tags = [tag for tag in analysis.get('tags', []) if not should_filter_tag(tag)]
                if tags:
                    f.write("\n")
                    for tag in tags:
                        f.write(f"![{tag}](https://img.shields.io/badge/-{tag.replace(' ', '_')}-brightgreen) ")
                    f.write("\n")
                
                # 启动配置使用代码块
                f.write("\n<details>\n<summary>启动配置</summary>\n\n")
                
                if 'server_command' in analysis:
                    f.write("```json\n")
                    f.write(json.dumps(analysis['server_command'], 
                                     ensure_ascii=False, 
                                     indent=2))
                    f.write("\n```\n")
                
                # 环境变量参数
                params = analysis.get('params', [])
                if params:
                    f.write("\n**必需参数:**\n")
                    for param in params:
                        f.write(f"- `{param}`\n")
                
                f.write("</details>\n\n")
                f.write("---\n\n")
            
            f.write("</details>\n\n")
            first_category = False
                
    print(f"已生成Awesome MCP Servers文档: {AWESOME_FILE}")

def main():
    # 加载服务器数据
    print("正在加载MCP服务器数据...")
    servers = load_mcp_servers()
    if not servers:
        print("未找到符合条件的MCP服务器")
        return
        
    print(f"找到 {len(servers)} 个高质量MCP服务器(评分 >= {MIN_SCORE})")
    
    # 保存过滤后的结果
    save_filtered_servers(servers)
    
    # 按标签聚类
    print("正在按标签聚类服务器...")
    clusters = get_tag_clusters(servers)
    print(f"共识别出 {len(clusters)} 个主要领域类别")
    
    # 生成Awesome文档
    print("正在生成Awesome MCP Servers文档...")
    generate_awesome_md(clusters)
    
    print("处理完成!")

if __name__ == "__main__":
    main()