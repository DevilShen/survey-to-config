"""配置推导规则包。

按配置分组拆模块，与《报事报修配置说明书》第 7 章的 7 个分组一一对应：
org(C01-06) · catalog(C07-13) · flow(C14-25) · sla(C26-40) ·
approval(C41-49) · fee(C50-60) · priority(C61-81)

新增规则时只需在对应模块里加一个 @rule 函数并挂进 derive()，
不要在分组之间互相引用——跨组矛盾统一走 analytics/conflicts.py。
"""
