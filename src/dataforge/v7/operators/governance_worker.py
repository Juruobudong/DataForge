"""Trusted worker-only argument conversion. No eval, exec, imports from user input."""
import operator
import math


def lock_network():
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError("OPERATOR_NETWORK_DISABLED: 算子子进程禁止联网，模型调用仅允许宿主Serving代理")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def predicates(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        raise ValueError("FILTER_RULE_INVALID: rules must be a nonempty bounded list")
    comparisons = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
    result = []
    for index, rule in enumerate(rules):
        operation, value, field = rule.get("operator"), rule.get("value"), f"rule_{index}"
        if operation in comparisons:
            compare = comparisons[operation]
            result.append(lambda frame, key=field, expected=value, fn=compare: frame[key].map(lambda current: bool(fn(current, expected))))
        elif operation == "contains":
            result.append(lambda frame, key=field, expected=value: frame[key].map(lambda current: expected in current))
        elif operation == "in":
            result.append(lambda frame, key=field, expected=value: frame[key].isin(expected))
        elif operation in {"is_empty", "not_empty"}:
            result.append(lambda frame, key=field, empty=operation == "is_empty": frame[key].map(lambda current: (current is None or isinstance(current, float) and math.isnan(current) or current == "" or current == []) == empty))
        else:
            raise ValueError("FILTER_RULE_INVALID: operator is not allowlisted")
    return result


def prepare_init(request, init):
    adapter = request.get("adapter_version")
    if adapter == "governance-conditions-v1":
        init["filter_rules"] = predicates(init.pop("rules", None))
    if adapter == "governance-blocklist-v1":
        init.pop("blocklist", None)
    if adapter in {"governance-privacy_filter-v1", "governance-anonymize-v1"}:
        from pathlib import Path
        root = Path(request["resource_bundle"]["root"])
        init.update(lang="en", device="cpu", model_cache_dir=str(root / "hf" / "hub"))
        import tldextract
        tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    if str(adapter).startswith("governance-"):
        # Some upstream constructors try to download even when tokenization is
        # disabled. All corpora must be provisioned by the maintainer beforehand.
        import nltk
        def deny_download(*args, **kwargs):
            raise ValueError("OPERATOR_RESOURCE_MISSING: NLTK资源必须预先安装，运行时禁止下载")
        nltk.download = deny_download
    return init


def configure_operator(request, instance):
    if request.get("adapter_version") == "governance-blocklist-v1":
        words = request.get("init", {}).get("blocklist")
        if words:
            if not isinstance(words, list) or any(not isinstance(word, str) or not word.strip() for word in words):
                raise ValueError("FILTER_RULE_INVALID: invalid blocklist")
            instance.blocklist = {word.strip().lower() for word in words}
