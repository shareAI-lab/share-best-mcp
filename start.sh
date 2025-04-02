#!/bin/bash

# 爬取MCP服务器，记录在mcp_basic_repos.jsonl中
python mcp_crawler_step_1.py

# 爬取mcp_basic_repos.jsonl中的MCP服务器，获取README内容，记录在mcp_repos_readme.jsonl中
python mcp_crawler_step_2.py

# 分析mcp_repos_readme.jsonl中的MCP服务器，记录在results/mcp_repos_analysis.jsonl中
python mcp_analyzer_step_3.py
