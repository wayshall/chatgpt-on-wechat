# encoding:utf-8

import time

import openai
import os
from openai import OpenAI
import json

from bot.bot import Bot
from .moonshot_session import MoonshotSession
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf
from pathlib import Path

# OpenAI对话模型API (可用)
class MoonshotOpenAIBot(Bot):
    def __init__(self):
        super().__init__()
        self.model = conf().get("model") or "moonshot-v1-128k"
        self.sessions = SessionManager(MoonshotSession, model=self.model)
        self.args = {
            "temperature": conf().get("temperature", 0.3),  # 如果设置，值域须为 [0, 1] 我们推荐 0.3，以达到较合适的效果。
            "top_p": conf().get("top_p", 1.0),  # 使用默认值
        }
        self.api_key = conf().get("moonshot_api_key")
        self.base_url = conf().get("moonshot_base_url", "https://api.moonshot.cn/v1/chat/completions")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        self._clear_files()

    def _clear_files(self):
        file_list = self.client.files.list()

        for file in file_list.data:
            logger.info("Deleting file: {}".format(file.id))
            self.client.files.delete(file_id=file.id)

    def reply(self, query, context=None):
        # acquire reply content
        if context and context.type:
            if context.type == ContextType.TEXT:
                logger.info("[OPEN_AI] query={}".format(query))
                session_id = context["session_id"]
                reply = None
                if query == "#清除记忆":
                    self.sessions.clear_session(session_id)
                    reply = Reply(ReplyType.INFO, "记忆已清除")
                elif query == "#清除所有":
                    self.sessions.clear_all_session()
                    reply = Reply(ReplyType.INFO, "所有人记忆已清除")
                else:
                    session = self.sessions.session_query(query, session_id)
                    new_args = self.new_args_from_context(context, session)
                    logger.info("session message: \n{}".format(json.dumps(session.messages, ensure_ascii=False)))
                    result = self.reply_text(session, new_args)
                    total_tokens, completion_tokens, reply_content = (
                        result["total_tokens"],
                        result["completion_tokens"],
                        result["content"],
                    )
                    logger.debug(
                        "[OPEN_AI] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(str(session), session_id, reply_content, completion_tokens)
                    )

                    if total_tokens == 0:
                        reply = Reply(ReplyType.ERROR, reply_content)
                    else:
                        self.sessions.session_reply(reply_content, session_id, total_tokens)
                        reply = Reply(ReplyType.TEXT, reply_content)
                return reply
            else:
                reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
                return reply

    def reply_text(self, session: MoonshotSession, args=None, retry_count=0):
        try:
            response = self.client.chat.completions.create(
                messages=session.messages,
                **args
            )
            res_content = response.choices[0]["text"].strip().replace("<|endoftext|>", "")
            total_tokens = response["usage"]["total_tokens"]
            completion_tokens = response["usage"]["completion_tokens"]
            logger.info("[OPEN_AI] reply={}".format(res_content))
            return {
                "total_tokens": total_tokens,
                "completion_tokens": completion_tokens,
                "content": res_content,
            }
        except Exception as e:
            logger.error("[OPEN_AI] reply_text error: {}".format(e))
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            if isinstance(e, openai.RateLimitError):
                logger.warn("[OPEN_AI] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, openai.Timeout):
                logger.warn("[OPEN_AI] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, openai.APIConnectionError):
                logger.warn("[OPEN_AI] APIConnectionError: {}".format(e))
                need_retry = False
                result["content"] = "我连接不到你的网络"
            else:
                logger.warn("[OPEN_AI] Exception: {}".format(e))
                need_retry = False
                self.sessions.clear_session(session.session_id)

            if need_retry:
                logger.warn("[OPEN_AI] 第{}次重试".format(retry_count + 1))
                return self.reply_text(session, args, retry_count + 1)
            else:
                return result

    def new_args_from_context(self, context, session):
        model = context.get("moonshot_model")
        if not model:
            model = self.model
        new_args = self.args.copy()
        if "model" not in new_args:
            new_args["model"] = model
        file_dir = context.get("file_dir")
        # 如果文件目录存在，则读取该目录下的所有文件，并添加到session.messages
        if file_dir and os.path.exists(file_dir) and not session.files_loaded:
            for file in os.listdir(file_dir):
                file_path = os.path.join(file_dir, file)
                if os.path.isfile(file_path):
                    file_object = self.client.files.create(file=Path(file_path), purpose="file-extract")
                    file_content = self.client.files.content(file_id=file_object.id).text
                    session.messages.append({
                        "role": "system",
                        "content": file_content,
                    })
                    session.files_loaded = True
        return new_args