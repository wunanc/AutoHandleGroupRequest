import json
import asyncio
import websockets
import aiohttp
import yaml
from datetime import datetime
from typing import Dict
import signal

class SimpleAutoApproveWS:
    """WebSocket单连接版自动审核"""
    
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        self.ws_url = self.config.get('bot', {}).get('websocket_url', 'ws://127.0.0.1:3001')
        self.access_token = self.config.get('bot', {}).get('access_token', '')
        
        self.min_qq_level = self.config.get('rules', {}).get('min_qq_level', 5)
        self.enable_level_check = self.config.get('rules', {}).get('enable_level_check', True)
        self.reject_keywords = self.config.get('keywords', {}).get('reject', [])
        self.approve_keywords = self.config.get('keywords', {}).get('approve', [])
        
        self.group_whitelist = self.config.get('group_whitelist', [])
        
        # WS
        self.ws = None
        
        self.should_exit = False
        
        self.print_config()
    
    def print_config(self):
        print("=" * 50)
        print("QQ群自动审核机器人")
        print("仓库: https://github.com/wunanc/AutoHandleGroupRequest")
        print("喜欢的话记得点个Star哦！")
        print("=" * 50)
        print(f"WebSocket地址: {self.ws_url}")
        print(f"最低QQ等级: {self.min_qq_level}")
        print(f"等级检查功能: {'启用' if self.enable_level_check else '禁用'}")
        print(f"拒绝关键词数: {len(self.reject_keywords)}")
        print(f"同意关键词数: {len(self.approve_keywords)}")
        print(f"群聊白名单数量: {len(self.group_whitelist)}")
        if self.group_whitelist:
            print(f"处理的群聊: {', '.join(str(g) for g in self.group_whitelist)}")
        else:
            print("处理的群聊: 所有群聊")
        print("=" * 50)
        print("程序已启动,正在连接NapCat...")
        print("提示: 按 Ctrl+C 可退出程序")
        print("=" * 50)
    
    #检查群
    def is_group_whitelisted(self, group_id: int) -> bool:
        if not self.group_whitelist:
            return True
        return group_id in self.group_whitelist
    
    #获取等级
    async def get_qq_level(self, user_id: int) -> int:
        try:
            url = f"https://api.mmp.cc/api/Query?qq={user_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if data.get("code") == 0:
                            level_info = data.get("data", {}).get("level_info", {})
                            level_str = level_info.get("iQQLevel", "0")
                            
                            try:
                                return int(level_str)
                            except ValueError:
                                print(f"解析QQ等级失败: {level_str}")
                                return 0
                        else:
                            print(f"API返回错误: {data.get('msg', '未知错误')}")
                            return 0
                    else:
                        print(f"HTTP请求失败: {response.status}")
                        return 0
                        
        except Exception as e:
            print(f"获取QQ等级出错: {e}")
            return 0
    
    def check_keywords(self, text: str) -> str:
        if not text:
            return "skip"
        
        text_lower = text.lower()
        
        for keyword in self.reject_keywords:
            if keyword.lower() in text_lower:
                return "reject"
        
        for keyword in self.approve_keywords:
            if keyword.lower() in text_lower:
                return "approve"
        
        return "skip"
    
    async def handle_group_request(self, flag: str, sub_type: str, approve: bool, reason: str = ""):
        try:
            api_request = {
                "action": "set_group_add_request",
                "params": {
                    "flag": flag,
                    "sub_type": sub_type,
                    "approve": approve
                }
            }
            
            if not approve and reason:
                api_request["params"]["reason"] = reason
            
            await self.ws.send(json.dumps(api_request))
            return True
            
        except Exception as e:
            print(f"处理请求出错: {e}")
            return False
    
    def format_log(self, action: str, group_id: int, user_id: int, level: int, comment: str, reason: str = ""):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        action_map = {
            "reject": "拒绝",
            "approve": "同意",
            "skip": "跳过"
        }
        
        action_text = action_map.get(action, action)
        
        log_line = f"[{timestamp}] 群{group_id} 用户{user_id}"
        
        if level > 0:
            log_line += f"(QQ等级:{level})"
        
        log_line += f" 申请内容: {comment}"
        
        if action == "reject":
            log_line += f" -> 自动{action_text}"
            if "等级" in reason:
                log_line += f" (原因:{reason})"
            else:
                log_line += f" (关键词触发)"
        elif action == "approve":
            log_line += f" -> 自动{action_text} (关键词触发)"
        else:
            log_line += f" -> 不处理，留给人工审核"
        
        return log_line
    
    async def process_request(self, data: Dict):
        try:
            group_id = data.get("group_id")
            user_id = data.get("user_id")
            comment = data.get("comment", "")
            flag = data.get("flag")
            sub_type = data.get("sub_type")
            
            if not all([group_id, user_id, flag, sub_type]):
                return
            
            if not self.is_group_whitelisted(group_id):
                return
            
            qq_level = 0
            
            if self.enable_level_check:
                qq_level = await self.get_qq_level(user_id)
                if qq_level == 0 or qq_level < self.min_qq_level:
                    await self.handle_group_request(flag, sub_type, False, f"QQ等级低于{self.min_qq_level}级")
                    print(self.format_log("reject", group_id, user_id, qq_level, comment, f"QQ等级{qq_level}低于{self.min_qq_level}"))
                    return
            
            keyword_result = self.check_keywords(comment)
            
            if keyword_result == "reject":
                await self.handle_group_request(flag, sub_type, False, "拒绝") #以后我想在配置文件里加个拒绝理由的选项
                print(self.format_log("reject", group_id, user_id, qq_level, comment))
            elif keyword_result == "approve":
                await self.handle_group_request(flag, sub_type, True)
                print(self.format_log("approve", group_id, user_id, qq_level, comment))
            else:
                print(self.format_log("skip", group_id, user_id, qq_level, comment))
                
        except Exception as e:
            print(f"处理请求时出错: {e}")
    
    async def handle_message(self, message: str):
        try:
            data = json.loads(message)
            
            if data.get("post_type") == "request" and data.get("request_type") == "group":
                sub_type = data.get("sub_type")
                if sub_type in ["add", "invite"]:
                    await self.process_request(data)
                    
        except json.JSONDecodeError:
            print(f"无法解析JSON消息: {message[:100]}")
        except Exception as e:
            print(f"处理消息时出错: {e}")
    
    async def connect_to_napcat(self):
        try:
            url = self.ws_url
            if self.access_token:
                url = f"{url}?access_token={self.access_token}"
            
            print(f"正在连接到: {url}")
            
            async with websockets.connect(url) as websocket:
                self.ws = websocket
                print("成功连接到NapCat！")
                print("等待加群请求事件...")
                
                async for message in websocket:
                    if self.should_exit:
                        print("正在关闭连接...")
                        break
                    await self.handle_message(message)
                    
        except Exception as e:
            if not self.should_exit:
                print(f"连接出错: {e}")
    
    async def start(self):
        await self.connect_to_napcat()

async def main():
    bot = SimpleAutoApproveWS("config.yaml")
    
    def signal_handler(signum, frame):
        print(f"\n收到终止信号,正在退出程序...")
        bot.should_exit = True
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        await bot.start()
    except Exception as e:
        if not bot.should_exit:
            print(f"程序运行出错: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已完全退出")