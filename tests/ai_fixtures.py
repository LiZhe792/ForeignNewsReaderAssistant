"""Explicitly synthetic model responses, used only by automated tests."""


def context_result(selected):
    return {
        'contextual_meaning': '这里的 bank 指河岸；这是测试服务给出的受控学习示例。',
        'sense_disambiguation': '与表示银行的义项不同，应结合 river 等语境判断。',
        'phrase_meaning': '按原文中的整个表达理解，而不是逐词机械拼接。',
        'figurative_language': '若用于比喻，应区分字面含义与语境含义。',
        'register_tone': '这里使用中性说明语气。',
        'evidence': selected,
        'learning_note': '请核对原文线索；解释深度由请求中的学习水平指定。',
        'uncertainty': '测试结果不代表真实模型质量评估。',
        'references': [{'expression': 'it', 'referent': '前文提到的对象（须结合上下文）', 'evidence': selected}],
        'examples': [{'en': 'The readers rested on the river bank.', 'zh': '读者们在河岸休息。'}],
    }


def translation_result(selected):
    return {'literal': '直译测试：' + selected, 'natural': '意译测试：这段文字描述了阅读与河岸的情景。',
            'comparison': '直译保留表达结构，意译调整中文语序。', 'cultural_note': '如无可靠背景依据，不额外补充文化事实。',
            'uncertainty': '', 'expressions': [{'expression': selected[:40], 'explanation': '结合原文理解的重点表达。'}]}
