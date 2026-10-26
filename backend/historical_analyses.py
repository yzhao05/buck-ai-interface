"""Full historical analysis replies, explicitly distinct from calculated results."""
import re


def explicit_label(content, field):
    # Ignore generic mentions of PANN/integral in explanations. Only explicit
    # labelled declarations qualify, and conflicting declarations stay unknown.
    plain=content.replace("`", "").replace("**", "")
    if field == "method":
        pattern=r"(?:\bmethod|辨识方法|计算方法|使用方法|方法)\s*[:：=]\s*(pann\b|weighted_integral\b|integral\b|抗噪加权积分|加权积分|积分辨识)"
        mapping={"pann":"pann", "weighted_integral":"weighted_integral", "integral":"integral",
                 "抗噪加权积分":"weighted_integral", "加权积分":"weighted_integral", "积分辨识":"integral"}
    else:
        pattern=r"(?:\bpwm_source|PWM\s*(?:来源|源))\s*[:：=]\s*(measured\b|controller\b|实测|实际|控制器重建|重建)"
        mapping={"measured":"measured", "controller":"controller", "实测":"measured", "实际":"measured",
                 "控制器重建":"controller", "重建":"controller"}
    matches=list(re.finditer(pattern,plain,re.I))
    # A list of possible methods/sources is not a declaration of the one used.
    if any(re.match(r"\s*(?:/|或|与|和|、|\band\b|\bor\b)",plain[m.end():],re.I) for m in matches):
        return None
    values={mapping[m.group(1).lower()] for m in matches}
    return next(iter(values)) if len(values)==1 else None


def analysis_context(analyses, total):
    replies=[]
    for item in analyses:
        method=item.method or explicit_label(item.content,"method")
        pwm=item.pwm_source or explicit_label(item.content,"pwm_source")
        replies.append({"id":item.id,"request_id":item.request_id,"result_id":item.result_id,
            "method":method,"pwm_source":pwm,"content":item.content,
            "method_source":"recorded_request" if item.method else "explicit_reply" if method else "not_recorded",
            "pwm_source_evidence":"recorded_request" if item.pwm_source else "explicit_reply" if pwm else "not_recorded"})
    groups={"same_pwm":{},"same_method":{},"unclassified":[]}
    for reply in replies:
        if reply["method"] and reply["pwm_source"]:
            groups["same_pwm"].setdefault(reply["pwm_source"],[]).append(reply["id"])
            groups["same_method"].setdefault(reply["method"],[]).append(reply["id"])
        else:
            groups["unclassified"].append(reply["id"])
    return {"source":"historical_assistant_answers", "available_count":max(total,len(replies)),
            "selected_count":len(replies), "selection":"latest_up_to_four_result_analysis_replies",
            "replies":replies,"comparison_groups":groups}


def local_analysis_history(context):
    if not context["replies"]:
        return "本会话未找到可配对的历史结果分析回答。请确认分析仍保存在当前会话中；这不代表必须重新运行辨识。本次没有启动计算。"
    lines=[f"已找回最近 {context['selected_count']} 条完整结果分析（本会话共 {context['available_count']} 条）。依据来自历史 AI 回答，不是本次重新计算。", "",
           "| 历史分析 | 方法 | PWM 来源 |", "|---|---|---|"]
    for index, reply in enumerate(context["replies"],1):
        method={"pann":"PANN","weighted_integral":"抗噪加权积分","integral":"积分辨识"}.get(reply["method"],"未记录")
        pwm={"measured":"实测 PWM","controller":"重建 PWM"}.get(reply["pwm_source"],"未记录")
        lines.append(f"| {index} | {method} | {pwm} |")
    lines.extend(["", "当前为本地检索结果，尚未生成自动差异分析。AI 服务可用时，将依据这些完整回答比较同一 PWM 下的两种方法，以及同一种方法下的两种 PWM 来源；未记录的数值不补造。"])
    return "\n".join(lines)
