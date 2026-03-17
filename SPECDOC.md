# Roundtable Meeting System V1 规格文档

## 1. 文档目标

本文档定义一个 **多 Agent 圆桌会议系统 V1** 的最小可行版本（MVP）。

目标不是构建一个复杂的审议引擎，而是模拟一种更接近“真人专家圆桌会”的讨论形式：

* 用户提出议题
* 多个 Agent 作为专家自由发言
* 仲裁者引导节奏、组织讨论、进行评分
* 通过 3 轮主讨论，必要时补 1 轮
* 最终输出清晰的讨论结论、推荐方案、保留意见与贡献评分

V1 核心追求：

1. **简单可实现**
2. **讨论有增量**
3. **结论有收敛**
4. **能兼顾创新和批判**

---

## 2. V1 设计原则

### 2.1 会议感优先于工程复杂度

V1 不追求复杂状态机、复杂评估器、多层仲裁，而是追求一种自然的会议式流程。

### 2.2 讨论价值来自两个核心动作

在 V1 中，评价 Agent 发言质量的核心只有两项：

1. **新颖性（Novelty）**：有没有提出新的、有价值的观点
2. **漏洞发现（Critique）**：有没有指出别人观点中的关键弱点

### 2.3 仲裁者只做三件事

仲裁者（Moderator / Arbiter）不深度参与业务分析，只负责：

1. 引导话题
2. 每轮做总结
3. 给每个 Agent 打分，并输出最终方案

### 2.4 默认 3 轮，必要时补第 4 轮

* 3 轮为标准流程
* 只有在仍有高价值未解分歧时才进入第 4 轮

### 2.5 自由发言，但每轮有明确任务

系统不做复杂的定向配对辩论，但每轮都给出明确任务要求，防止并行独白。

---

## 3. 系统角色

## 3.1 User

职责：

* 提出议题
* 可附带目标、限制条件、背景信息

输入示例：

* “如何设计一个多 agent 协作做股票研究的系统？”
* “这个交易策略最近失效的主要原因是什么？”
* “我们如何设计一个更高效的新闻分析工作流？”

---

## 3.2 Moderator / Arbiter

职责：

1. 会议开场
2. 议题标准化
3. 控制轮次
4. 每轮总结
5. 为每个 Agent 打分
6. 判断是否进入第 4 轮
7. 产出最终总结报告

限制：

* 不应主导具体业务内容
* 不应在前两轮直接给出最终答案
* 重点是组织讨论和归纳结果

---

## 3.3 Experts / Agents

建议数量：**3 到 6 个**

职责：

* 围绕议题提出观点
* 阅读他人观点后进行补充、批评、辩护和修正
* 尽可能推动讨论往更优方案收敛

V1 中不强制复杂角色分工，但建议给每个 Agent 一个大致风格标签，例如：

* 创新型（偏提出新想法）
* 审慎型（偏发现漏洞）
* 工程型（偏落地实现）
* 专业型（偏领域知识）

说明：
V1 不要求这些角色高度刚性，只作为轻量 Prompt 风格差异来源。

---

## 4. 总体流程

系统流程如下：

1. Moderator 开场
2. Round 1：各自独立提出初始观点
3. Moderator 总结 + 评分
4. Round 2：提出新观点 + 攻击别人弱点 + 认可值得保留的点
5. Moderator 总结 + 评分
6. Round 3：回应攻击 + 修正观点 + 给出最终保留主张
7. Moderator 总结 + 评分
8. 判断是否进入 Round 4
9. 如果需要，则进行 Round 4：补充关键未解问题
10. Moderator 输出最终方案

---

## 5. 轮次定义

## 5.1 Round 0：Moderator 开场

目标：

* 把用户议题变成会议可讨论对象
* 说明会议目标与规则
* 明确本次讨论关注什么，不关注什么

输出内容：

1. 议题重述
2. 本次会议目标
3. 判断一个好答案的标准
4. 当前轮规则说明

示例输出：

```text
议题：如何设计一个多 Agent 圆桌讨论系统。
目标：形成一个既能产出新观点，又能充分暴露方案漏洞，最终能输出清晰结论的会议机制。
标准：创新性、严谨性、收敛性、可落地性。
规则：第一轮每位专家先独立陈述观点，不互相回应。
```

---

## 5.2 Round 1：独立初始观点

目标：

* 最大化初始多样性
* 避免早期锚定
* 为后续交锋提供素材

规则：

* 所有 Agent 独立发言
* 不回应别人
* 只输出自己的初步判断

每个 Agent 必须回答：

1. 我对这个议题的核心主张是什么
2. 我认为最关键的 1-3 个观点是什么
3. 我认为该议题里最重要的问题或风险是什么
4. 我的初步建议是什么

推荐输出格式：

```json
{
  "core_position": "...",
  "key_points": ["...", "..."],
  "main_risks": ["..."],
  "initial_suggestion": "..."
}
```

---

## 5.3 Round 2：新观点 + 攻击弱点 + 保留他人亮点

目标：

* 让讨论从并行独白转为交叉互动
* 引入新的增量观点
* 暴露初始方案中的薄弱点

规则：

* 所有 Agent 可以看到 Round 1 全部发言
* 每个 Agent 必须提出新观点
* 每个 Agent 必须攻击至少 1 个别人的具体观点
* 每个 Agent 必须指出至少 1 个值得保留的别人观点

每个 Agent 必须回答：

1. 看完大家发言后，我新增的 1-2 个观点是什么
2. 我认为谁的哪条观点最薄弱
3. 具体漏洞是什么
4. 哪个别人的观点最值得保留

推荐输出格式：

```json
{
  "new_points": ["...", "..."],
  "attacks": [
    {
      "target_agent": "Agent_B",
      "target_point": "...",
      "weakness": "..."
    }
  ],
  "preserved_points": [
    {
      "source_agent": "Agent_C",
      "point": "...",
      "reason": "..."
    }
  ]
}
```

---

## 5.4 Round 3：辩护 + 修正 + 收敛

目标：

* 回应关键攻击
* 承认合理批评并修正
* 将立场推向收敛

规则：

* 所有 Agent 可以看到 Round 2 全部发言
* 每个 Agent 必须回应对自己的关键攻击
* 每个 Agent 必须明确哪些批评被接受
* 每个 Agent 必须给出自己的最终保留主张

每个 Agent 必须回答：

1. 别人对我最有力的攻击是什么
2. 我接受哪些批评，并如何修正
3. 我最终仍然保留的核心主张是什么
4. 我现在支持的最终方案倾向是什么

推荐输出格式：

```json
{
  "strongest_attack_on_me": "...",
  "accepted_criticisms": ["..."],
  "revisions": ["..."],
  "final_position": "...",
  "preferred_solution": "..."
}
```

---

## 5.5 Round 4（可选）：关键未解问题补充轮

触发条件：

* 还有明显高价值新观点未展开
* 仍存在关键未解决冲突
* 仲裁者认为当前方案未收敛

目标：

* 只讨论剩余 1-2 个关键问题
* 避免继续泛化扩散

规则：

* Moderator 必须明确指出本轮只讨论什么
* 每个 Agent 仅围绕指定问题发言
* 不再做大范围发散

推荐输出格式：

```json
{
  "focused_issue": "...",
  "final_addition": "...",
  "last_attack_or_defense": "...",
  "closing_view": "..."
}
```

---

## 6. 评分机制

V1 评分机制保持极简。

## 6.1 每轮评分维度

### Novelty Score（0-5）

评价标准：

* 是否提出了新的、有价值的观点
* 是否推动了讨论向前发展
* 是否只是在重复别人

评分参考：

* 0：完全重复，没有新增价值
* 1：轻微补充，但没有关键新意
* 2：有一定增量，但较普通
* 3：提出了有用的新角度
* 4：提出明显重要的新观点
* 5：提出高价值、可能改变讨论方向的新观点

### Critique Score（0-5）

评价标准：

* 是否识别出别人观点中的关键漏洞
* 批评是否具体
* 是否真的击中了核心问题，而不是泛泛挑刺

评分参考：

* 0：没有批评，或批评无意义
* 1：只有表面质疑
* 2：指出了一般性问题
* 3：指出了真实且具体的弱点
* 4：指出重要漏洞并影响方案可信度
* 5：准确击中核心缺陷，显著改变讨论格局

---

## 6.2 最终综合评分

在会议结束时，Moderator 给每个 Agent 一个 **Contribution Score（0-10）**。

评价标准：

* 全程是否持续提供有价值内容
* 是否推动了方案优化
* 是否对最终结论产生影响
* 是否只是重复或噪声输出

---

## 6.3 每轮评分输出格式

建议 Moderator 对每个 Agent 输出：

```json
{
  "agent": "Agent_A",
  "novelty_score": 4,
  "critique_score": 3,
  "comment": "提出了一个新的机制视角，并对他人方案提出了较具体的质疑。"
}
```

---

## 7. Moderator 每轮任务

在每轮结束后，Moderator 必须做以下工作：

1. 总结本轮新增的关键观点
2. 总结本轮最有力的攻击
3. 指出哪些观点值得保留
4. 给每个 Agent 打 Novelty / Critique 分
5. 决定是否进入下一轮

推荐输出模板：

```markdown
## Round N Summary

### New valuable ideas
- ...
- ...

### Strong critiques
- Agent B 对 Agent A 的质疑：...
- Agent C 对 Agent D 的攻击：...

### Points worth preserving
- ...
- ...

### Scores
- Agent A: Novelty 4 / Critique 2
- Agent B: Novelty 2 / Critique 5
- Agent C: Novelty 3 / Critique 3

### Next step
进入下一轮，重点关注：...
```

---

## 8. 最终输出要求

会议结束后，Moderator 输出最终报告。

最终报告必须包含：

1. **问题定义**
2. **主要共识**
3. **主要分歧**
4. **最终推荐方案**
5. **为什么推荐该方案**
6. **保留意见**
7. **各 Agent 贡献总结**
8. **最终评分表**

推荐模板：

```markdown
# Final Roundtable Report

## 1. Problem Definition
...

## 2. Main Consensus
...

## 3. Main Disagreements
...

## 4. Recommended Solution
...

## 5. Why This Solution
...

## 6. Preserved Minority Opinions
...

## 7. Agent Contributions
- Agent A: ...
- Agent B: ...
- Agent C: ...

## 8. Final Scores
- Agent A: Contribution 8/10
- Agent B: Contribution 7/10
- Agent C: Contribution 9/10
```

---

## 9. 数据结构建议

V1 不需要复杂对象系统，但建议至少定义以下数据结构。

## 9.1 会议输入

```python
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class MeetingInput:
    topic: str
    goal: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    background: Optional[str] = None
```

---

## 9.2 Agent 发言记录

```python
@dataclass
class AgentMessage:
    round_index: int
    agent_name: str
    content: Dict[str, Any]
    raw_text: str = ""
```

---

## 9.3 每轮评分记录

```python
@dataclass
class RoundScore:
    round_index: int
    agent_name: str
    novelty_score: int
    critique_score: int
    comment: str
```

---

## 9.4 最终评分

```python
@dataclass
class FinalScore:
    agent_name: str
    contribution_score: int
    summary: str
```

---

## 9.5 最终报告

```python
@dataclass
class FinalReport:
    problem_definition: str
    main_consensus: List[str]
    main_disagreements: List[str]
    recommended_solution: str
    why_this_solution: str
    preserved_minority_opinions: List[str]
    agent_contributions: Dict[str, str]
    final_scores: List[FinalScore]
    raw_markdown: str = ""
```

---

## 10. Agent 接口设计

建议统一一个最小接口：

```python
class BaseMeetingAgent:
    name: str
    role_label: str
    system_prompt: str

    async def speak(self, round_index: int, task_prompt: str, context: dict) -> AgentMessage:
        ...
```

说明：

* `round_index`：当前轮次
* `task_prompt`：当前轮次任务说明
* `context`：允许看到的上下文
* 返回值统一为 `AgentMessage`

---

## 11. Orchestrator 设计

V1 建议只做一个简单 orchestrator。

```python
class RoundtableMeeting:
    def __init__(self, moderator, experts, max_rounds=4):
        self.moderator = moderator
        self.experts = experts
        self.max_rounds = max_rounds
        self.messages = []
        self.scores = []
```

### 核心方法

```python
async def run(self, meeting_input: MeetingInput) -> FinalReport:
    opening = await self.moderator_opening(meeting_input)
    
    r1 = await self.run_round_1(meeting_input)
    s1 = await self.moderator_summarize_and_score(round_index=1)
    
    r2 = await self.run_round_2(meeting_input)
    s2 = await self.moderator_summarize_and_score(round_index=2)
    
    r3 = await self.run_round_3(meeting_input)
    s3 = await self.moderator_summarize_and_score(round_index=3)
    
    if await self.moderator_should_continue():
        r4 = await self.run_round_4(meeting_input)
        s4 = await self.moderator_summarize_and_score(round_index=4)
    
    final_report = await self.moderator_finalize(meeting_input)
    return final_report
```

---

## 12. 上下文传递规则

V1 中不应把所有历史原文无限堆给每个 Agent。

建议：

### Round 1

提供：

* 用户议题
* Moderator 开场说明

### Round 2

提供：

* 用户议题
* Round 1 所有 Agent 的结构化发言摘要
* Round 1 Moderator 总结

### Round 3

提供：

* 用户议题
* Round 2 所有 Agent 的结构化发言摘要
* Round 2 Moderator 总结
* 与当前 Agent 相关的对其攻击内容

### Round 4

提供：

* 用户议题
* 当前只讨论的关键未解问题
* 前面 3 轮的高度压缩总结

原则：

* 传摘要，不传冗长原文
* 保留关键攻击和关键观点
* 防止 token 膨胀和注意力涣散

---

## 13. Prompt 模板建议

## 13.1 Moderator Opening Prompt

```text
你是这场圆桌会议的仲裁者和主持人。
请根据用户议题完成开场：
1. 重述议题
2. 明确本次会议目标
3. 给出判断好答案的标准
4. 说明本轮规则

保持中立、简洁、结构化。
不要提前给出最终答案。
```

---

## 13.2 Expert Round 1 Prompt

```text
你是圆桌会议中的一位专家。
现在是第一轮，请独立提出你的初始观点。
要求：
1. 给出你的核心主张
2. 给出最关键的 1-3 个观点
3. 说明你认为本议题最大的风险或问题
4. 给出你的初步建议

注意：
- 第一轮不要回应别人
- 重点是独立判断
- 尽量清晰具体
```

---

## 13.3 Expert Round 2 Prompt

```text
你现在可以看到其他专家在第一轮的观点。
请进行第二轮发言，必须完成以下三项：
1. 提出 1-2 个新的观点
2. 攻击至少 1 个别人的具体观点，指出其关键漏洞
3. 指出至少 1 个你认为值得保留的别人观点

注意：
- 攻击要具体，不要泛泛批评
- 新观点要有清晰价值
- 不要只重复第一轮内容
```

---

## 13.4 Expert Round 3 Prompt

```text
你现在可以看到第二轮中别人对你的批评，以及全场新的讨论内容。
请进行第三轮发言，必须完成以下内容：
1. 回应别人对你最有力的攻击
2. 承认并修正合理的批评
3. 给出你最终仍然保留的核心主张
4. 给出你当前支持的最终方案倾向

注意：
- 不要只做防御
- 要体现修正和收敛
- 目标是帮助形成更强的最终方案
```

---

## 13.5 Moderator Summary Prompt

```text
你是圆桌会议仲裁者。
请对本轮讨论做总结，并完成以下任务：
1. 总结本轮新增的关键观点
2. 总结本轮最有力的攻击
3. 指出哪些观点值得保留
4. 对每位专家给出 Novelty Score 和 Critique Score
5. 简短说明是否进入下一轮，以及下一轮重点

评分范围：0-5。
保持中立、清晰、可读。
```

---

## 13.6 Moderator Final Prompt

```text
你是这场圆桌会议的仲裁者。
请基于所有轮次内容输出最终总结报告，包含：
1. 问题定义
2. 主要共识
3. 主要分歧
4. 最终推荐方案
5. 为什么推荐该方案
6. 保留意见
7. 各专家贡献总结
8. 各专家最终贡献评分

要求：
- 中立
- 清晰
- 有结论
- 不只是复述发言
```

---

## 14. JSON 解析建议

为了便于程序处理，建议专家发言尽量使用 JSON 或稳定的结构化字段。

如果实际运行中 JSON 稳定性不好，可以采用两段式策略：

1. 先让模型输出带固定标题的 markdown
2. 再由 parser 抽取结构化字段

例如：

* `Core Position:`
* `New Points:`
* `Attacks:`
* `Accepted Criticisms:`
* `Final Position:`

V1 阶段不要把 JSON repair 做得太复杂。
只要能稳定解析主要字段即可。

---

## 15. 是否进入 Round 4 的判断规则

V1 采用简单规则，不做复杂计算。

Moderator 可依据以下标准决定：

进入 Round 4 的条件：

1. 仍存在至少一个关键分歧未解
2. 该分歧会显著影响最终方案
3. 还有继续讨论的价值，而不是简单重复

如果只是重复争论，没有新东西，就不进入 Round 4。

可用一句简化判断：

```text
如果还有“重要但未解决”的问题，就开第 4 轮；否则结束。
```

---

## 16. MVP 开发边界

V1 必须做：

1. 用户议题输入
2. Moderator 开场
3. 3 轮主流程
4. 可选第 4 轮
5. 每轮总结
6. 每轮 Novelty / Critique 打分
7. 最终报告输出

V1 不做：

1. 复杂 dispute map
2. 多 evaluator 架构
3. 动态角色权重
4. 多层状态机
5. 自动语义去重
6. 长期 memory
7. 高级角色编排

原因：
V1 的目标是先验证这个会议机制是否能稳定产出高质量结果。

---

## 17. 默认参数建议

* 专家数量：`3~6`
* 默认轮次：`3`
* 最大轮次：`4`
* 每轮每个 Agent 发言次数：`1`
* 每轮总结次数：`1`
* 每轮评分范围：`0~5`
* 最终贡献评分范围：`0~10`

---

## 18. 成功标准

V1 是否成功，不看系统是否复杂，而看以下几点：

1. 第一轮是否能产生明显不同的初始观点
2. 第二轮是否真的发生有效交叉批评
3. 第三轮是否出现修正和收敛
4. 最终输出是否比单 Agent 回答更全面、更经打磨
5. Moderator 总结是否可读、可用、可直接给用户

---

## 19. 开发顺序建议

推荐开发顺序：

### Step 1

实现基础数据结构：

* MeetingInput
* AgentMessage
* RoundScore
* FinalScore
* FinalReport

### Step 2

实现 Moderator 与 Expert 的统一接口

### Step 3

实现 3 轮固定流程

### Step 4

实现每轮 Moderator 总结与评分

### Step 5

实现最终报告输出

### Step 6

实现可选 Round 4

### Step 7

补 parser、日志、失败重试

---

## 20. 一句话总结

**Roundtable Meeting System V1** 的本质不是“复杂多智能体编排”，而是：

> 用一个简单的三轮专家会议机制，让多个 Agent 先独立发言，再互相启发与攻击，最后由仲裁者完成收敛、评分和总结。

这就是 V1 应该实现的全部重点。
