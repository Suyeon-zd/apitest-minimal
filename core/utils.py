# core/utils.py - 框架通用工具函数


def replace_vars(obj, variables):
    """递归替换字符串中的 ${var} 变量引用（用例间 extract → ${var} 传值）

    支持 str / dict / list；非字符串原样返回。
    注意：本函数处理的是"用例运行时变量"，与 config_loader 的环境变量插值
    （.env / 系统环境变量）是两个正交机制，互不干扰。
    变量值为 None 时保留 ${var} 占位不替换，避免被 str(val) 变成字符串 "None" 发到接口。
    """
    if isinstance(obj, str):
        for key, val in variables.items():
            if val is None:
                continue
            obj = obj.replace(f"${{{key}}}", str(val))
        return obj
    if isinstance(obj, dict):
        return {k: replace_vars(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_vars(item, variables) for item in obj]
    return obj
