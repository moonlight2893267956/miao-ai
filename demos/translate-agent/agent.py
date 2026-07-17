"""
翻译通用 Agent (translate-agent)

一个 agent + 可配置 task 的「AI 增强层」：站在百度机器翻译之上，用 LLM 补足
机械翻译做不到的通顺/风格化/上下文连贯。

支持三个任务（按 input.task 分发）：
- translate：百度机器翻译打底 + LLM 润色（mt_provider=baidu，默认）；或纯 LLM 翻译（mt_provider=llm）
- polish：在已有文本上做风格化润色（不改语义只改表达）
- context：带前文上下文 + 术语库的连贯翻译（解决指代/术语一致）

工程模式复用 json-repairer：
- _build_llm()：max_retries=0 + timeout，禁用 SDK 内部重试
- TOTAL_TIMEOUT + _check_timeout(stage)：agent 总超时守护
- MAX_LLM_CALLS：LLM 调用失败重试
- _normalize_llm_output()：剥离 markdown fence
- 流式：config["stream"] 为 True 时逐 token yield {"token": ...}
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, List, Optional

import hashlib
import random
import requests
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# 单文件打包：baidu_client / prompts 已内联到本文件末尾，无需 import 同目录模块。
# 仍注入本目录到 sys.path 以兼容各类运行环境（无害）。
import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

logger = logging.getLogger("translate-agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

MODEL = os.environ.get("LLM_MODEL", os.environ.get("DASHSCOPE_MODEL", "qwen-plus"))
API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("DASHSCOPE_API_KEY", ""))
BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("DASHSCOPE_BASE_URL", ""))
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", os.environ.get("DASHSCOPE_TEMPERATURE", "0.3")))
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", os.environ.get("DASHSCOPE_MAX_TOKENS", "4096")))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))

# 一次调用中最多 2 次 LLM 尝试（含首次），避免级联延迟
MAX_LLM_CALLS = 2
# 单次 agent 调用总超时，必须在平台 invoke_sync_timeout 之内（默认 300s）
TOTAL_TIMEOUT = int(os.environ.get("AGENT_TOTAL_TIMEOUT", "240"))

VALID_TASKS = {"translate", "polish", "context"}

logger.info(
    "Config: model=%s, base_url=%s, temp=%s, max_tokens=%s, timeout=%ss",
    MODEL,
    BASE_URL[:30] if BASE_URL else "(empty)",
    TEMPERATURE,
    MAX_TOKENS,
    LLM_TIMEOUT,
)


# -------- LLM 构建与输出标准化（复用 json-repairer 模式）--------

def _build_llm() -> ChatOpenAI:
    """构建 LLM 实例。max_retries=0 禁用 SDK 内部重试，agent 自己重试。"""
    kwargs = dict(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        api_key=API_KEY or "sk-placeholder",
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return ChatOpenAI(**kwargs)


def _strip_markdown_fence(text: str) -> str:
    """安全剥离 markdown code fence。"""
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    result_lines = lines[1:]
    while result_lines and result_lines[-1].strip() in ("```", "``"):
        result_lines.pop()
    return "\n".join(result_lines).strip()


def _normalize_llm_output(text: str) -> str:
    return _strip_markdown_fence((text or "").strip())


def _call_llm(llm: ChatOpenAI, system_prompt: str, user_content: str) -> str:
    """调用 LLM 并标准化输出，带一次重试（复用 MAX_LLM_CALLS 模式）。"""
    last_err: Optional[Exception] = None
    for attempt in range(MAX_LLM_CALLS):
        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_content),
            ]
            response = llm.invoke(messages)
            out = _normalize_llm_output(response.content)
            if out:
                return out
            last_err = ValueError("LLM 返回空响应")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM call attempt %d failed: %s", attempt + 1, e)
    raise ValueError(f"LLM 调用失败（{MAX_LLM_CALLS} 次尝试）：{last_err}")


def _stream_llm(llm: ChatOpenAI, system_prompt: str, user_content: str):
    """流式调用 LLM，逐 token yield {"token": ...}。"""
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    for chunk in llm.stream(messages):
        if chunk.content:
            yield {"token": chunk.content}


# -------- 输入解析 --------

def _parse_input(input: dict) -> dict:
    task = (input.get("task") or "translate").strip().lower()
    if task not in VALID_TASKS:
        task = "translate"
    return {
        "task": task,
        "text": input.get("text", "") or "",
        "original": input.get("original", "") or "",
        "source_lang": input.get("source_lang", "auto") or "auto",
        "target_lang": input.get("target_lang", "en") or "en",
        "tone": input.get("tone"),
        "domain": input.get("domain"),
        "glossary": input.get("glossary") or [],
        "context": input.get("context", "") or "",
        "mt_provider": (input.get("mt_provider") or "baidu").strip().lower(),
    }


# -------- 各任务的 user content 构造 --------

def _translate_user_content(text: str, mt_draft: Optional[str], source_lang: str) -> str:
    if mt_draft is not None:
        return f"原文（{source_lang}）：\n{text}\n\n机器译文：\n{mt_draft}"
    return text


def _context_user_content(text: str, context: str) -> str:
    return f"前文上下文：\n{context}\n\n待翻译文本：\n{text}"


# -------- 三任务实现 --------

def _do_translate(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    """百度机器翻译打底 + LLM 润色；或纯 LLM 翻译。"""
    mt_draft: Optional[str] = None
    baidu_note = ""

    if p["mt_provider"] != "llm":
        client = BaiduTranslateClient()
        if client.configured:
            try:
                check_timeout("baidu")
                mt_draft = client.translate(
                    p["text"], p["source_lang"], p["target_lang"]
                )
            except BaiduTranslateError as e:
                # 百度失败优雅降级到纯 LLM 翻译，不崩溃
                logger.warning("Baidu translate failed, fallback to LLM: %s", e)
                baidu_note = f"百度翻译降级（{e}），已改用 LLM 直接翻译"
        else:
            baidu_note = "百度未配置密钥，已改用 LLM 直接翻译"

    check_timeout("llm_translate")
    system = build_translate_prompt(
        p["target_lang"], p["tone"], p["domain"], p["glossary"],
        has_mt_draft=mt_draft is not None,
    )
    user_content = _translate_user_content(p["text"], mt_draft, p["source_lang"])
    translated = _call_llm(llm, system, user_content)

    result = {
        "task": "translate",
        "translated": translated,
        "source_lang": p["source_lang"],
        "target_lang": p["target_lang"],
        "bilingual": [{"src": p["text"], "tgt": translated}],
    }
    if mt_draft is not None:
        result["mt_draft"] = mt_draft
    if baidu_note:
        result["notes"] = baidu_note
    return result


def _do_polish(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    """在已有文本上做风格化润色（不改语义）。"""
    check_timeout("llm_polish")
    system = build_polish_prompt(p["target_lang"], p["tone"], p["domain"], p["glossary"])
    user_content = p["text"]
    if p["original"]:
        user_content = f"原文（供参考，勿改变语义）：\n{p['original']}\n\n待润色文本：\n{p['text']}"
    polished = _call_llm(llm, system, user_content)
    return {
        "task": "polish",
        "translated": polished,
        "polished": polished,
        "notes": "已按指定风格润色，保持原语义",
    }


def _do_context(llm: ChatOpenAI, p: dict, check_timeout) -> dict:
    """带前文上下文 + 术语库的连贯翻译。"""
    check_timeout("llm_context")
    system = build_context_prompt(p["target_lang"], p["tone"], p["domain"], p["glossary"])
    user_content = _context_user_content(p["text"], p["context"])
    translated = _call_llm(llm, system, user_content)
    result = {
        "task": "context",
        "translated": translated,
        "source_lang": p["source_lang"],
        "target_lang": p["target_lang"],
        "bilingual": [{"src": p["text"], "tgt": translated}],
    }
    if p["glossary"]:
        result["glossary_applied"] = p["glossary"]
    return result


# -------- 流式路径：仅对 LLM 部分逐 token yield --------

def _stream_invoke(p: dict, llm: ChatOpenAI):
    task = p["task"]
    if task == "translate":
        mt_draft: Optional[str] = None
        if p["mt_provider"] != "llm":
            client = BaiduTranslateClient()
            if client.configured:
                try:
                    mt_draft = client.translate(
                        p["text"], p["source_lang"], p["target_lang"]
                    )
                except BaiduTranslateError as e:
                    logger.warning("Baidu translate failed (stream), fallback: %s", e)
        system = build_translate_prompt(
            p["target_lang"], p["tone"], p["domain"], p["glossary"],
            has_mt_draft=mt_draft is not None,
        )
        user_content = _translate_user_content(p["text"], mt_draft, p["source_lang"])
    elif task == "polish":
        system = build_polish_prompt(p["target_lang"], p["tone"], p["domain"], p["glossary"])
        user_content = p["text"]
        if p["original"]:
            user_content = f"原文（供参考，勿改变语义）：\n{p['original']}\n\n待润色文本：\n{p['text']}"
    else:  # context
        system = build_context_prompt(p["target_lang"], p["tone"], p["domain"], p["glossary"])
        user_content = _context_user_content(p["text"], p["context"])

    yield from _stream_llm(llm, system, user_content)


# -------- 标准入口 --------

def invoke(input: dict, config: dict = None):
    """miao-ai Agent 标准入口。

    非流式：返回 dict；流式（config["stream"]=True）：返回 generator 逐 token yield。
    """
    config = config or {}
    p = _parse_input(input)

    if not p["text"]:
        return {"task": p["task"], "translated": "", "answer": "", "notes": "输入文本为空"}

    logger.info(
        "Invoke: task=%s len=%d %s->%s mt=%s stream=%s",
        p["task"], len(p["text"]), p["source_lang"], p["target_lang"],
        p["mt_provider"], bool(config.get("stream")),
    )

    llm = _build_llm()

    # 流式模式
    if config.get("stream"):
        return _stream_invoke(p, llm)

    # 非流式模式：超时守护
    t0 = time.time()

    def _check_timeout(stage: str) -> None:
        if time.time() - t0 > TOTAL_TIMEOUT:
            raise TimeoutError(f"Agent 总超时（{TOTAL_TIMEOUT}s），阶段：{stage}")

    if p["task"] == "translate":
        result = _do_translate(llm, p, _check_timeout)
    elif p["task"] == "polish":
        result = _do_polish(llm, p, _check_timeout)
    else:
        result = _do_context(llm, p, _check_timeout)

    logger.info("Invoke done: task=%s %.2fs", p["task"], time.time() - t0)
    # 顶层 answer 便于前端 Try Run（普通模式取 output.answer）直接展示译文；
    # 流式模式仍逐 token 输出，不受影响。translated 等字段保留向后兼容。
    result["answer"] = result.get("translated", "")
    return result


# ============================================================================
# 以下为内联依赖（原 baidu_client.py 与 prompts.py），
# 合并为单文件以兼容平台 Docker 构建（平台模板仅 COPY agent.py）。
# ============================================================================


# --------------------------- baidu_client ---------------------------
logger_baidu = logging.getLogger("translate-agent.baidu")

BAIDU_ENDPOINT = os.environ.get(
    "BAIDU_TRANSLATE_ENDPOINT",
    "https://fanyi-api.baidu.com/api/trans/vip/translate",
)
BAIDU_APPID = os.environ.get("BAIDU_TRANSLATE_APPID", "")
BAIDU_SECRET = os.environ.get("BAIDU_TRANSLATE_SECRET", "")
BAIDU_TIMEOUT = int(os.environ.get("BAIDU_TRANSLATE_TIMEOUT", "10"))

BAIDU_ERROR_MESSAGES = {
    "52001": "百度翻译请求超时，请重试",
    "52002": "百度翻译系统错误，请重试",
    "52003": "百度翻译未授权：appid 无效或未开通服务",
    "54000": "百度翻译必填参数为空",
    "54001": "百度翻译签名错误（请检查 appid/secret）",
    "54003": "百度翻译访问频率受限，请降低调用频率",
    "54004": "百度翻译账户余额不足",
    "54005": "长 query 请求频繁，请稍后再试",
    "58000": "百度翻译客户端 IP 非法",
    "58001": "百度翻译不支持的语种方向",
    "58002": "百度翻译服务当前已关闭",
    "90107": "百度翻译认证未通过或未生效",
}


class BaiduTranslateError(RuntimeError):
    """百度翻译调用失败（网络/签名/额度等），带上错误码便于上层降级。"""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


def _make_sign(appid: str, query: str, salt: str, secret: str) -> str:
    """MD5 签名：md5(appid + q + salt + secret)。"""
    raw = f"{appid}{query}{salt}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class BaiduTranslateClient:
    """百度通用文本翻译客户端。"""

    def __init__(
        self,
        appid: Optional[str] = None,
        secret: Optional[str] = None,
        endpoint: str = BAIDU_ENDPOINT,
        timeout: int = BAIDU_TIMEOUT,
        usage_recorder: Optional[Callable[[int, str, str], None]] = None,
    ):
        self.appid = appid or BAIDU_APPID
        self.secret = secret or BAIDU_SECRET
        self.endpoint = endpoint
        self.timeout = timeout
        # 预留字符级审计接口：MVP 默认 _log_usage，仅打日志
        self.usage_recorder = usage_recorder or self._log_usage

    @property
    def configured(self) -> bool:
        """是否已配置密钥（未配置时上层应降级到纯 LLM 翻译）。"""
        return bool(self.appid and self.secret)

    def _log_usage(self, char_count: int, source_lang: str, target_lang: str) -> None:
        logger_baidu.info(
            "baidu usage: chars=%d %s->%s", char_count, source_lang, target_lang
        )

    def translate(
        self,
        text: str,
        source_lang: str = "auto",
        target_lang: str = "en",
    ) -> str:
        """调用百度翻译，返回拼接后的译文。

        Raises:
            BaiduTranslateError: 未配置密钥、网络错误或百度返回错误码时。
        """
        if not self.configured:
            raise BaiduTranslateError(
                "百度翻译未配置：请设置 BAIDU_TRANSLATE_APPID / BAIDU_TRANSLATE_SECRET",
                code="unconfigured",
            )
        if not text:
            return ""

        salt = str(random.randint(10_000_000_000, 99_999_999_999))
        sign = _make_sign(self.appid, text, salt, self.secret)
        payload = {
            "q": text,
            "from": source_lang or "auto",
            "to": target_lang,
            "appid": self.appid,
            "salt": salt,
            "sign": sign,
        }

        t0 = time.time()
        try:
            resp = requests.post(self.endpoint, data=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise BaiduTranslateError(f"百度翻译网络错误：{e}", code="network") from e
        except ValueError as e:
            raise BaiduTranslateError(f"百度翻译响应解析失败：{e}", code="decode") from e

        if "error_code" in data:
            code = str(data.get("error_code"))
            friendly = BAIDU_ERROR_MESSAGES.get(
                code, f"百度翻译错误（code={code}）：{data.get('error_msg', '')}"
            )
            raise BaiduTranslateError(friendly, code=code)

        results = data.get("trans_result", [])
        translated = "\n".join(item.get("dst", "") for item in results)

        # 字符级审计（预留接口，MVP 仅日志）
        try:
            self.usage_recorder(len(text), source_lang, target_lang)
        except Exception:  # 审计失败不影响主流程
            logger_baidu.warning("usage_recorder failed", exc_info=True)

        logger_baidu.info(
            "baidu translate ok: %s->%s chars=%d %.2fs",
            source_lang,
            target_lang,
            len(text),
            time.time() - t0,
        )
        return translated


# --------------------------- prompts ---------------------------
LANG_NAMES = {
    "auto": "自动识别",
    "zh": "中文",
    "en": "英语",
    "jp": "日语",
    "ja": "日语",
    "kor": "韩语",
    "ko": "韩语",
    "fra": "法语",
    "fr": "法语",
    "de": "德语",
    "spa": "西班牙语",
    "es": "西班牙语",
    "ru": "俄语",
    "th": "泰语",
    "vie": "越南语",
    "ara": "阿拉伯语",
}

TONE_HINTS = {
    "formal": "正式、书面",
    "casual": "口语、轻松自然",
    "business": "商务、专业得体",
    "marketing": "营销、有吸引力的表达（突出卖点、号召力）",
    "literary": "文学、优美流畅",
    "academic": "学术、严谨精确",
}

DOMAIN_HINTS = {
    "tech": "技术/软件领域，保留专有名词与代码标识符不译",
    "medical": "医疗领域，术语需准确规范",
    "legal": "法律领域，措辞需严谨、无歧义",
    "finance": "金融领域，数字与术语需准确",
}


def lang_name(code: Optional[str]) -> str:
    if not code:
        return "目标语言"
    return LANG_NAMES.get(code, code)


def _style_directives(tone: Optional[str], domain: Optional[str]) -> str:
    parts: List[str] = []
    if tone:
        parts.append(f"- 语气风格：{TONE_HINTS.get(tone, tone)}")
    if domain:
        parts.append(f"- 领域约束：{DOMAIN_HINTS.get(domain, domain)}")
    return "\n".join(parts)


def _glossary_directives(glossary: Optional[List[dict]]) -> str:
    if not glossary:
        return ""
    lines = ["以下术语必须严格按对照表翻译（左=原文，右=指定译法）："]
    for pair in glossary:
        src = pair.get("src", "")
        tgt = pair.get("tgt", "")
        if src and tgt:
            lines.append(f"- {src} → {tgt}")
    return "\n".join(lines)


TRANSLATE_SYSTEM = """你是一名专业翻译专家。你的任务是把用户给出的文本翻译成 {target} 并保证地道、通顺、忠实原意。

要求：
- 忠实传达原文含义，不增删信息
- 译文符合 {target} 的表达习惯，避免翻译腔
- 保留原文的段落与换行结构
- 只输出译文本身，不要任何解释、不要 markdown 代码块"""

TRANSLATE_POLISH_SYSTEM = """你是一名专业翻译润色专家。下面给出「原文」与一份「机器译文」。请在忠实于原文的前提下，把机器译文改写得更地道、通顺、自然。

要求：
- 以原文语义为准，修正机器译文的生硬、错译、漏译
- 译文符合 {target} 的表达习惯
- 保留段落与换行结构
- 只输出润色后的最终译文，不要解释、不要 markdown 代码块"""

POLISH_SYSTEM = """你是一名文字润色专家。请对给定文本进行润色，使其更通顺、更符合目标风格，但不得改变原有语义。

要求：
- 不增删核心信息，只优化表达
- 只输出润色后的文本，不要解释、不要 markdown 代码块"""

CONTEXT_SYSTEM = """你是一名专业翻译专家，擅长长文档与对话的连贯翻译。下面给出「前文上下文」与「待翻译文本」。请把待翻译文本翻译成 {target}，并与上下文保持连贯。

要求：
- 保持术语、人名、代词指代在全文的一致性
- 参考上下文消解歧义（如 it/they 指代什么）
- 只翻译「待翻译文本」，不要翻译或复述上下文
- 只输出译文本身，不要解释、不要 markdown 代码块"""


def build_translate_prompt(
    target_lang: Optional[str],
    tone: Optional[str],
    domain: Optional[str],
    glossary: Optional[List[dict]],
    has_mt_draft: bool,
) -> str:
    """构造 translate 任务的 system prompt。

    has_mt_draft=True 表示已有百度机器译文，走「润色」路径；否则纯 LLM 翻译。
    """
    base = (TRANSLATE_POLISH_SYSTEM if has_mt_draft else TRANSLATE_SYSTEM).format(
        target=lang_name(target_lang)
    )
    extra = "\n".join(filter(None, [_style_directives(tone, domain), _glossary_directives(glossary)]))
    return f"{base}\n\n{extra}" if extra else base


def build_polish_prompt(
    target_lang: Optional[str],
    tone: Optional[str],
    domain: Optional[str],
    glossary: Optional[List[dict]],
) -> str:
    extra_parts = []
    if target_lang and target_lang != "auto":
        extra_parts.append(f"- 目标语言：{lang_name(target_lang)}")
    extra_parts.append(_style_directives(tone, domain))
    extra_parts.append(_glossary_directives(glossary))
    extra = "\n".join(filter(None, extra_parts))
    return f"{POLISH_SYSTEM}\n\n{extra}" if extra else POLISH_SYSTEM


def build_context_prompt(
    target_lang: Optional[str],
    tone: Optional[str],
    domain: Optional[str],
    glossary: Optional[List[dict]],
) -> str:
    base = CONTEXT_SYSTEM.format(target=lang_name(target_lang))
    extra = "\n".join(filter(None, [_style_directives(tone, domain), _glossary_directives(glossary)]))
    return f"{base}\n\n{extra}" if extra else base
