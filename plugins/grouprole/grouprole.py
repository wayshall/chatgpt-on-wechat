# encoding:utf-8
import json

import plugins
from bridge.bridge import Bridge
from common import const
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from common.log import logger
from plugins import *
from config import conf
from bot.bot_factory import create_bot

class GroupRolePlay:
    def __init__(self, role):
        self.role_desc = role["role_desc"]
        self.group_name = role["group_name"]
        self.bot_type = None
        if "tools" in role:
            self.tools = role["tools"]
        else:
            self.tools = None
        if "wrapper" in role:
            self.wrapper = role["wrapper"]
        else:
            self.wrapper = None
        if "bot_type" in role:
            self.bot_type = role["bot_type"]
            self.bot = create_bot(self.bot_type)
        if "model" in role:
            self.model = role["model"]
            if not self.bot_type:
                self.bot_type = GroupRolePlay.get_bot_type(self.model)
                self.bot = create_bot(self.bot_type)
            self.bot.args["model"] = self.model
        else:
            self.bot = None
        if "file_dir" in role:
            self.file_dir = role["file_dir"]
        else:
            self.file_dir = None

    def reset(self, bot, sessionid):
        bot.sessions.clear_session(sessionid)

    def action(self, bot, sessionid, user_action):
        session = bot.sessions.build_session(sessionid)
        if session.system_prompt != self.role_desc:  # 目前没有触发session过期事件，这里先简单判断，然后重置
            session.reset_with_prompt(self.role_desc)
        if self.wrapper:
            prompt = self.wrapper % user_action
        else:
            prompt = user_action
        return prompt

    @staticmethod
    def get_bot_type(model_type):
        bot_type = None
        if model_type in ["text-davinci-003"]:
            bot_type = const.OPEN_AI
        if conf().get("use_azure_chatgpt", False):
            bot_type = const.CHATGPTONAZURE
        if model_type in ["wenxin", "wenxin-4"]:
            bot_type = const.BAIDU
        if model_type in ["xunfei"]:
            bot_type = const.XUNFEI
        if model_type in [const.QWEN]:
            bot_type = const.QWEN
        if model_type in [const.QWEN_TURBO, const.QWEN_PLUS, const.QWEN_MAX]:
            bot_type = const.QWEN_DASHSCOPE
        if model_type in [const.GEMINI]:
            bot_type = const.GEMINI
        if model_type in const.ZHIPU_AI_MODELS:
            bot_type = const.ZHIPU_AI
        if model_type and model_type.startswith("claude-3"):
            bot_type = const.CLAUDEAPI
        if model_type in ["claude"]:
            bot_type = const.CLAUDEAI
        if model_type in ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]:
            bot_type = const.MOONSHOT
        if model_type in const.BAICHUAN_MODELS:
            bot_type = const.BAICHUAN

        if not bot_type:
            raise Exception("model_type is not supported: %s" % model_type)

        return bot_type

@plugins.register(
    name="Grouprole",
    desire_priority=-1,
    hidden=True,
    desc="load role by group",
    version="0.1",
    author="wayshall",
)
class Grouprole(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[GroupRole] inited")
        self.config = super().load_config()
        current_dir = os.path.dirname(__file__)
        config_path = os.path.join(current_dir, "grouproles.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                group_config = json.load(f)
                logger.info("[GroupRole] load grouproles.json success")

            self.group_roles = {}
            for role in group_config["roles"]:
                group_role = GroupRolePlay(role)
                self.group_roles[group_role.group_name] = group_role
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                logger.warn(f"[GroupRole] init failed, file not found: {config_path}")
            else:
                logger.error("[GroupRole] init failed", e)
            raise e
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT
        ]:
            return

        bot = Bridge().get_bot("chat")
        #  bridge.context.Context
        context = e_context["context"]
        sessionid = e_context["context"]["session_id"]
        content = context.content
        group_name = e_context["context"]["msg"].other_user_nickname

        logger.info("[GroupRole] on_handle_context group: %s" % group_name)
        if group_name not in self.group_roles:
            return
        group_role = self.group_roles[group_name]
        if group_role.bot:
            # logger.debug("[GroupRole] model not found for group: %s" % group_name)
            context["bot"] = group_role.bot
            bot = group_role.bot
            # 自定义了bot的基于知识库的问答每次都清除上下文，防止被上下文搞崩。。。
            # bot.sessions.clear_session(sessionid)
            # return

        prompt = group_role.action(bot, sessionid, content)
        context.type = ContextType.TEXT
        context.content = prompt

        if group_role.tools:
            context["tools"] = group_role.tools
        if group_role.file_dir:
            context["file_dir"] = group_role.file_dir

        e_context.action = EventAction.BREAK # 事件结束，进入默认处理逻辑，一般会覆写reply

    def get_help_text(self, **kwargs):
        help_text = "group role"
        return help_text
