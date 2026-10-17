"""
查询规划器

LLM 驱动的复杂查询解析，将自然语言查询分解为:
- 实体/关键词识别与扩展
- 硬约束与软偏好提取
- 意图识别
- 针对各数据源的子查询生成
- 检索策略选择
"""

import asyncio
import json
import logging
import re
from typing import Dict, List, Literal, Optional

from app.schemas.query import (
    Constraint,
    DataSource,
    QueryParseResult,
    SubQuery,
)
from app.services.llm.prompts import PromptTemplates

logger = logging.getLogger(__name__)


class QueryPlanner:
    """
    查询规划器

    短主题直接生成规则计划，复杂查询或显式 AI 模式才使用 LLM。
    当 LLM 不可用时，回退到基于规则的默认实现。
    """

    _PAPER_ALIASES = {
        "alphageometry": "Solving olympiad geometry without human demonstrations",
        "gpt2": "Language Models are Unsupervised Multitask Learners",
        "cnn": "ImageNet classification with deep convolutional neural networks",
        "ms2": "MS2: Multi-Document Summarization of Medical Studies",
        "squad": "SQuAD: 100,000+ Questions for Machine Comprehension of Text",
    }
    AI_TIMEOUT_SECONDS = 20.0
    PLAN_TIMEOUT_SECONDS = 21.0

    def __init__(self, llm_gateway=None):
        """
        初始化查询规划器

        Args:
            llm_gateway: LLM 网关实例（可选，为 None 时使用规则回退）
        """
        self.llm_gateway = llm_gateway
        self.planning_mode = "rules"
        self.planning_reason = "尚未规划"

    async def plan(
        self,
        query: str,
        sources: List[DataSource],
        user_constraints: Optional[List[Constraint]] = None,
        planning_mode: Literal["auto", "rules", "ai"] = "auto",
    ) -> QueryParseResult:
        """
        解析查询并生成检索计划

        Args:
            query: 用户的自然语言查询
            sources: 要检索的数据源列表
            user_constraints: 用户显式指定的约束条件（可选）
            planning_mode: 自动判断、仅规则或显式 AI 规划

        Returns:
            QueryParseResult 结构化解析结果
        """
        if planning_mode not in {"auto", "rules", "ai"}:
            raise ValueError("Invalid query planning mode")
        self.planning_mode = "rules"
        if not sources:
            self.planning_reason = "未选择数据源，不调用规划模型"
            return QueryParseResult(
                original_query=query,
                sub_queries=[],
                strategy="No data sources specified",
                intent="open_exploration",
                keywords=self._extract_keywords_rule(query),
                constraints=user_constraints or [],
            )

        rule_plan = self._plan_with_rules(query, sources, user_constraints)

        if planning_mode == "rules":
            self.planning_reason = "快速检索：不调用规划模型"
        elif planning_mode == "auto" and rule_plan.intent == "exact_lookup":
            self.planning_reason = "自动规划：精确论文查询使用规则直检"
        elif planning_mode == "auto" and not self._needs_semantic_planning(query):
            self.planning_reason = "自动规划：短主题关键词使用规则直检"
        elif self.llm_gateway is None:
            self.planning_reason = "规划模型不可用，使用规则检索"
        else:
            self.planning_reason = "AI 语义规划：单次模型请求预算 20 秒，失败后使用规则检索"
            return await self._try_ai_plan(query, sources, user_constraints, rule_plan)
        rule_plan.strategy = f"{self.planning_reason}。{rule_plan.strategy}"
        return rule_plan

    @staticmethod
    def _needs_semantic_planning(query: str) -> bool:
        """A bounded, explainable heuristic, not a semantic quality classifier.

        Explicit comparisons/exclusions/questions and long descriptions benefit
        from decomposition. Dates, venues and short topic phrases alone do not.
        Users can always override this decision with the AI or rules mode.
        """
        semantic_signals = (
            r"[?？]|比较|对比|区别|排除|不包括|不包含|分别|同时|如何|为什么|哪些|"
            r"\b(?:compare|comparison|versus|exclude|excluding|without|how|why|which)\b|"
            r"\b(?:rather than|but not|similar to)\b"
        )
        return bool(
            re.search(semantic_signals, query, re.IGNORECASE)
            or len(re.findall(r"[\u4e00-\u9fff]", query)) > 24
            or len(re.findall(r"[a-zA-Z][a-zA-Z0-9'-]*", query)) > 10
            or len(query.strip()) > 180
        )

    async def _try_ai_plan(
        self, query: str, sources: List[DataSource],
        user_constraints: Optional[List[Constraint]], rule_plan: QueryParseResult,
    ) -> QueryParseResult:
        try:
            # One useful model attempt, not two routes that both expire before
            # a slower provider can respond. Source retrieval has its own budget.
            llm_plan = await asyncio.wait_for(
                self._plan_with_llm(query, sources, user_constraints),
                timeout=self.PLAN_TIMEOUT_SECONDS,
            )
            # 保留规则解析出的显式约束，避免 LLM 遗漏年份、venue 等硬条件。
            existing = {
                (item.key, item.operator, str(item.value))
                for item in llm_plan.constraints
            }
            for constraint in rule_plan.constraints:
                signature = (
                    constraint.key,
                    constraint.operator,
                    str(constraint.value),
                )
                if signature not in existing:
                    llm_plan.constraints.append(constraint)
            self.planning_mode = "ai"
            self.planning_reason = "AI 规划完成"
            llm_plan.strategy = f"{self.planning_reason}。{llm_plan.strategy}"
            return llm_plan
        except Exception as exc:
            # CancelledError propagates: cancelling search must not start a
            # replacement search plan. Failed calls may still have provider cost.
            self.planning_mode = "fallback"
            self.planning_reason = "AI 规划超时、不可用或返回无效计划，已使用规则检索；已尝试的模型请求可能产生费用"
            rule_plan.strategy = f"{self.planning_reason}。{rule_plan.strategy}"
            logger.warning(
                "LLM query planning failed; using deterministic plan: %s",
                type(exc).__name__,
            )
            return rule_plan

    def _build_prompt(self, query: str, sources: List[DataSource]) -> str:
        """
        构建查询解析的 LLM prompt

        Args:
            query: 用户查询
            sources: 数据源列表

        Returns:
            格式化后的 prompt 字符串
        """
        source_names = [s.value for s in sources]
        sources_str = ", ".join(source_names)
        return PromptTemplates.query_parse(query=query, sources=sources_str) + (
            "\nUse OR for synonyms within a concept and AND between different concepts. "
            "'Not prediction-only' does not mean excluding every paper mentioning prediction. "
            "Dates belong in constraints, not keyword strings. "
            "Do not invent unsupported constraint keys."
        )

    # ------------------------------------------------------------------
    # LLM 解析
    # ------------------------------------------------------------------

    async def _plan_with_llm(
        self,
        query: str,
        sources: List[DataSource],
        user_constraints: Optional[List[Constraint]],
    ) -> QueryParseResult:
        """使用 LLM 解析查询"""
        prompt = self._build_prompt(query, sources)

        response_text = await self.llm_gateway.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an academic search query parser. "
                        "Always respond with valid JSON only, no markdown fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
            timeout_seconds=self.AI_TIMEOUT_SECONDS,
            allow_fallback=False,
        )

        # 清理可能的 markdown 代码块包裹
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            # 去掉 ```json 和 ```
            lines = cleaned.split("\n")
            # 找到第一行 ``` 和最后一行 ```
            start = 0
            end = len(lines)
            for i, line in enumerate(lines):
                if line.strip().startswith("```"):
                    start = i + 1
                    break
            for i in range(len(lines) - 1, start, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            cleaned = "\n".join(lines[start:end])

        data = json.loads(cleaned)

        # 构建 sub_queries
        sub_queries = []
        source_set = {s.value for s in sources}
        covered_sources = set()
        for sq_data in data.get("sub_queries", []):
            source_val = sq_data.get("source", "")
            source_query = sq_data.get("query")
            if (source_val in source_set and source_val not in covered_sources
                    and isinstance(source_query, str) and 0 < len(source_query.strip()) <= 2000):
                sub_queries.append(SubQuery(
                    query=(self._openalex_query(source_query, original=query)
                           if source_val == "openalex" else source_query.strip()),
                    source=DataSource(source_val),
                    rationale=sq_data.get("rationale", ""),
                ))
                covered_sources.add(source_val)
        if not sub_queries:
            raise ValueError("AI query plan contains no usable source query")

        # 为 LLM 未覆盖的数据源补充子查询
        for source in sources:
            if source.value not in covered_sources:
                sub_queries.append(SubQuery(
                    query=query,
                    source=source,
                    rationale="Fallback: LLM did not generate a sub-query for this source",
                ))

        # 构建 constraints
        constraints = list(user_constraints) if user_constraints else []
        for c_data in data.get("constraints", []):
            constraints.append(Constraint(
                key=c_data.get("key", ""),
                operator=c_data.get("operator", "eq"),
                value=c_data.get("value"),
                description=(
                    "题名/摘要中的任务线索偏好，不是全文任务核验；缺少线索不等于违反条件"
                    if c_data.get("key") == "task_type" else c_data.get("description")
                ),
            ))

        # 提取 keywords（合并原始关键词和扩展关键词）
        keywords = data.get("keywords", [])
        expanded = data.get("expanded_keywords", {})
        for key, synonyms in expanded.items():
            if isinstance(synonyms, dict):
                # 新格式：{synonyms: [...], abbreviations: [...], ...}
                for category in synonyms.values():
                    if isinstance(category, list):
                        keywords.extend(category)
            elif isinstance(synonyms, list):
                keywords.extend(synonyms)
        # 去重保序
        seen = set()
        unique_keywords = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_keywords.append(kw)

        return QueryParseResult(
            original_query=query,
            sub_queries=sub_queries,
            strategy=data.get("strategy", "LLM-generated strategy"),
            intent=data.get("intent", "open_exploration"),
            keywords=unique_keywords,
            constraints=constraints,
            entities=self._extract_entities_rule(query, self._extract_keywords_rule(query)),
            expanded_queries=self._build_expanded_queries(
                query, self._extract_keywords_rule(query), data.get("intent", "open_exploration"),
            ),
        )

    # ------------------------------------------------------------------
    # 规则回退
    # ------------------------------------------------------------------

    def _plan_with_rules(
        self,
        query: str,
        sources: List[DataSource],
        user_constraints: Optional[List[Constraint]],
    ) -> QueryParseResult:
        """基于规则的查询解析（LLM 不可用时的回退）"""
        keywords = self._extract_keywords_rule(query)
        intent = self._detect_intent_rule(query)
        entities = self._extract_entities_rule(query, keywords)
        constraints = self._merge_constraints(
            user_constraints or [],
            self._extract_constraints_rule(query, entities),
        )
        expanded_queries = self._build_expanded_queries(query, keywords, intent)

        sub_queries = []
        for source in sources:
            sub_queries.append(SubQuery(
                query=self._optimize_query_for_source(query, keywords, source),
                source=source,
                rationale=self._generate_rationale(source, intent),
            ))

        strategy = self._generate_strategy(intent, sources)
        if any(
            re.search(r"[\u4e00-\u9fff]", self._translate_to_english(keyword))
            for keyword in keywords
        ):
            strategy += "；部分主题词未翻译，已保留原文，跨语言召回可能有限"

        return QueryParseResult(
            original_query=query,
            sub_queries=sub_queries,
            strategy=strategy,
            intent=intent,
            keywords=keywords,
            constraints=constraints,
            entities=entities,
            expanded_queries=expanded_queries,
        )

    def build_refinement_subqueries(
        self,
        plan: QueryParseResult,
        sources: List[DataSource],
    ) -> List[SubQuery]:
        """为低召回结果生成至多一组二轮查询，不触发额外 LLM 调用。"""
        facets = self.search_facets(plan.original_query)
        can_relax_method = facets["method"] and facets["task"] and facets["domain"]
        if not plan.expanded_queries and not can_relax_method:
            return []
        # Relax only the method in the existing second round; preserve domain
        # and task. Final ranking still checks the original requested method.
        refinement = (
            " ".join([*facets["domain"], facets["task"]])
            if can_relax_method
            else plan.expanded_queries[0]
        )
        initial = {(sq.source, sq.query.strip().lower()) for sq in plan.sub_queries}
        sub_queries: List[SubQuery] = []
        for source in sources:
            optimized = self._optimize_query_for_source(
                refinement,
                self._extract_keywords_rule(refinement),
                source,
            )
            if (source, optimized.strip().lower()) in initial:
                continue
            sub_queries.append(SubQuery(
                query=optimized,
                source=source,
                rationale="Recall recovery: bounded query expansion after a low-yield first pass",
            ))
        return sub_queries

    @staticmethod
    def _merge_constraints(
        explicit: List[Constraint],
        inferred: List[Constraint],
    ) -> List[Constraint]:
        """显式筛选优先，并对推断约束去重。"""
        merged = list(explicit)
        seen = {(c.key, c.operator, str(c.value).lower()) for c in explicit}
        for constraint in inferred:
            key = (constraint.key, constraint.operator, str(constraint.value).lower())
            if key not in seen:
                seen.add(key)
                merged.append(constraint)
        return merged

    @staticmethod
    def _extract_constraints_rule(
        query: str,
        entities: Dict[str, List[str]],
    ) -> List[Constraint]:
        """识别常见时间、引用量、开放获取和 venue 约束。"""
        import re

        constraints: List[Constraint] = []
        range_match = re.search(r"\b(19|20)\d{2}\s*[-–—至到]\s*((?:19|20)\d{2})\b", query)
        if range_match:
            start = int(range_match.group(0)[:4])
            end = int(range_match.group(2))
            constraints.extend([
                Constraint(key="year", operator="gte", value=start, description=f"Year >= {start}"),
                Constraint(key="year", operator="lte", value=end, description=f"Year <= {end}"),
            ])
        else:
            since = re.search(r"(?:since|after|from|近|自)\s*((?:19|20)\d{2})", query, re.I)
            before = re.search(r"(?:before|until|截至|早于)\s*((?:19|20)\d{2})", query, re.I)
            if since:
                constraints.append(Constraint(
                    key="year", operator="gte", value=int(since.group(1)),
                    description=f"Year >= {since.group(1)}",
                ))
            if before:
                constraints.append(Constraint(
                    key="year", operator="lte", value=int(before.group(1)),
                    description=f"Year <= {before.group(1)}",
                ))

        citations = re.search(
            r"(?:at least|minimum|不少于|至少)\s*(\d+)\s*(?:citations?|次引用|引用)",
            query,
            re.I,
        )
        if citations:
            constraints.append(Constraint(
                key="min_citations", operator="gte", value=int(citations.group(1)),
                description=f"Minimum {citations.group(1)} citations",
            ))

        if re.search(r"\bopen[\s-]?access\b|开放获取", query, re.I):
            constraints.append(Constraint(
                key="open_access", operator="eq", value=True,
                description="Open access only",
            ))

        venues = entities.get("venues", [])
        if venues:
            constraints.append(Constraint(
                key="venue", operator="in", value=venues,
                description=f"Published at one of: {', '.join(venues)}",
            ))
        return constraints

    @staticmethod
    def _extract_entities_rule(
        query: str,
        keywords: List[str],
    ) -> Dict[str, List[str]]:
        """低成本识别复杂学术查询的多维实体。"""
        import re

        def unique(values: List[str]) -> List[str]:
            seen = set()
            result = []
            for value in values:
                normalized = value.strip()
                key = normalized.lower()
                if normalized and key not in seen:
                    seen.add(key)
                    result.append(normalized)
            return result

        venue_pattern = (
            r"\b(?:ACL|EMNLP|NAACL|NeurIPS|ICML|ICLR|CVPR|ICCV|ECCV|"
            r"AAAI|IJCAI|KDD|WWW|SIGIR|CHI|Nature|Science)\b"
        )
        venues = re.findall(venue_pattern, query, re.I)
        datasets = re.findall(
            r"\b[A-Za-z][A-Za-z0-9._-]*(?:Bench|Benchmark|Dataset|Corpus|QA)\b",
            query,
        )
        datasets += re.findall(
            r"\b(?:ImageNet|CIFAR-?10|CIFAR-?100|COCO|SQuAD|MMLU|"
            r"RealScholarQuery|AutoScholar|PaperFindingBench|LitSearch)\b",
            query,
            re.I,
        )
        method_terms = [
            "transformer", "rag", "reinforcement learning", "federated learning",
            "graph neural network", "large language model", "llm", "diffusion",
            "contrastive learning", "self-supervised learning", "强化学习",
            "联邦学习", "图神经网络", "大语言模型", "检索增强生成", "对比学习",
        ]
        methods = [term for term in method_terms if term.lower() in query.lower()]
        years = re.findall(r"\b(?:19|20)\d{2}\b", query)
        topics = [kw for kw in keywords if kw.lower() not in {
            item.lower() for item in methods + venues + datasets
        }]
        return {
            "topics": unique(topics[:8]),
            "methods": unique(methods),
            "datasets": unique(datasets),
            "venues": unique(venues),
            "years": unique(years),
        }

    @classmethod
    def _build_expanded_queries(
        cls,
        query: str,
        keywords: List[str],
        intent: str,
    ) -> List[str]:
        """生成最多两个高信息密度扩展，限制二轮 API 成本。"""
        candidates: List[str] = []
        translated = cls._translate_to_english(query).strip()
        if translated and translated.lower() != query.strip().lower():
            candidates.append(translated)

        compact_terms = [cls._ZH_EN_MAP.get(kw, kw) for kw in keywords[:6]]
        compact = " ".join(dict.fromkeys(term for term in compact_terms if term)).strip()
        suffix = {
            "literature_review": "survey review",
            "methodology_survey": "benchmark evaluation",
            "similar_recommendation": "related work",
        }.get(intent, "")
        if compact:
            candidates.append(f"{compact} {suffix}".strip())

        result: List[str] = []
        seen = {query.strip().lower()}
        for candidate in candidates:
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                result.append(candidate)
        return result[:2]

    @classmethod
    def _extract_keywords_rule(cls, query: str) -> List[str]:
        """基于规则提取关键词，支持中英文混合查询"""
        import re

        # 英文停用词
        en_stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "can", "shall", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "and", "but", "or",
            "not", "no", "nor", "so", "yet", "both", "either", "neither", "each",
            "every", "all", "any", "few", "more", "most", "other", "some", "such",
            "than", "too", "very", "just", "about", "up", "out", "if", "then",
            "that", "this", "these", "those", "it", "its", "i", "me", "my",
            "we", "our", "you", "your", "he", "him", "his", "she", "her",
            "they", "them", "their", "what", "which", "who", "whom", "how",
            "when", "where", "why", "recent", "latest", "new", "paper", "papers",
            "study", "studies", "method", "methods", "approach", "approaches",
            "use", "uses", "used", "using",
            "give", "find", "provide", "suggest", "recommend", "please",
            "show", "share", "list", "could", "insights",
            "et", "al",
        }

        # 只过滤独立片段；不能用单字“大、中、数”等切碎未知领域名称。
        zh_stop_words = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
            "一", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
            "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那",
            "些", "么", "等", "而", "吗", "吧", "啊", "哦", "把", "被",
            "让", "给", "从", "向", "对", "以", "及", "或", "与", "但",
            "近", "年", "来", "中", "上", "下", "大", "小", "多", "少",
            "个", "种", "类", "样", "第", "每", "某", "该", "此",
            "其", "之", "所", "者", "里", "后", "前", "内", "外",
            "两", "实验", "对比", "方法", "有",
            "基于", "关于", "用于", "使用", "研究", "论文", "文献",
            "中的", "中的使用", "方面", "查找", "寻找", "请", "帮我",
        }

        query = cls.positive_query(query)
        keywords = []
        seen = set()

        # 提取英文词
        en_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]*", query)
        for token in en_tokens:
            lower = token.lower()
            if lower not in en_stop_words and len(lower) > 1 and lower not in seen:
                seen.add(lower)
                keywords.append(token)

        # 最长术语优先，保留词典外主题原文，不用通用 data/model 替代它。
        zh_text = re.sub(r"[a-zA-Z0-9\-\.]+", " ", query)
        terms = "|".join(re.escape(term) for term in sorted(cls._ZH_EN_MAP, key=len, reverse=True))
        for segment in re.findall(r"[\u4e00-\u9fff]+", zh_text):
            for part in re.split(f"({terms})", segment):
                if len(part) >= 2 and part not in seen and part not in zh_stop_words:
                    seen.add(part)
                    keywords.append(part)

        return keywords

    @staticmethod
    def _detect_intent_rule(query: str) -> str:
        """基于规则检测查询意图"""
        import re

        query_lower = query.lower()

        # 精确查找信号
        exact_signals = ["find the paper", "locate", "doi:", "arxiv:", "specific paper"]
        author_citation = re.search(
            r"\bby\s+[a-z][a-z\-']+(?:\s+et\s+al\.?)?",
            query_lower,
        )
        named_paper = re.match(r"^\s*the\s+.+\s+paper\s*$", query_lower)
        short_paper_name = (
            query_lower.rstrip(".").endswith(" paper")
            and len(re.findall(r"[a-z0-9]+", query_lower)) <= 6
        )
        paper_about = query_lower.startswith(("the paper about ", "a paper about "))
        if (
            any(s in query_lower for s in exact_signals)
            or author_citation
            or named_paper
            or short_paper_name
            or paper_about
        ):
            return "exact_lookup"

        # 综述/路线梳理信号
        review_signals = ["survey", "review", "overview", "taxonomy", "综述", "调研", "路线"]
        if any(s in query_lower for s in review_signals):
            return "literature_review"

        # 相似推荐信号
        similar_signals = ["similar to", "like", "related work", "comparable", "类似", "相似"]
        if any(s in query_lower for s in similar_signals):
            return "similar_recommendation"

        # 方法论调查信号
        method_signals = ["methodology", "benchmark", "evaluation", "comparison", "方法", "对比"]
        if any(s in query_lower for s in method_signals):
            return "methodology_survey"

        return "open_exploration"

    # 中文学术术语 → 英文对照
    _ZH_EN_MAP = {
        "联邦学习": "federated learning", "隐私保护": "privacy protection",
        "差分隐私": "differential privacy", "医疗数据": "medical data",
        "深度学习": "deep learning", "强化学习": "reinforcement learning",
        "自然语言处理": "natural language processing", "计算机视觉": "computer vision",
        "知识图谱": "knowledge graph", "迁移学习": "transfer learning",
        "大语言模型": "large language model", "神经网络": "neural network",
        "预训练大语言模型": "pretrained large language model",
        "预训练语言模型": "pretrained language model",
        "数据挖掘": "data mining", "图像识别": "image recognition",
        "推荐系统": "recommendation system", "对话系统": "dialogue system",
        "语音识别": "speech recognition", "机器翻译": "machine translation",
        "生成对抗": "generative adversarial", "注意力机制": "attention mechanism",
        "图神经网络": "graph neural network", "自监督学习": "self-supervised learning",
        "元学习": "meta-learning", "对比学习": "contrastive learning",
        "数据增强": "data augmentation", "模型压缩": "model compression",
        "边缘计算": "edge computing", "物联网": "internet of things",
        "区块链": "blockchain", "网络安全": "cybersecurity",
        "异常检测": "anomaly detection", "情感分析": "sentiment analysis",
        "目标检测": "object detection", "语义分割": "semantic segmentation",
        "医疗": "medical", "健康": "health", "诊断": "diagnosis",
        "药物": "drug", "临床": "clinical", "基因": "genome",
        "蛋白质": "protein", "细胞": "cell", "肿瘤": "tumor",
        "应用": "applications", "方法": "methods", "综述": "survey",
        "对比": "comparison", "实验": "experiment", "优化": "optimization",
        "框架": "framework", "系统": "system", "模型": "model",
        "算法": "algorithm", "网络": "network", "数据": "data",
        "科学文献": "scientific literature", "文献检索": "literature search",
        "论文检索": "paper retrieval", "论文搜索": "paper search",
        "论文推荐": "paper recommendation", "学术搜索": "academic search",
        "引文网络": "citation network", "相关性": "relevance",
        "交通流预测": "traffic flow prediction", "交通预测": "traffic prediction",
        "时序预测": "time series forecasting", "轨迹预测": "trajectory prediction",
        "交通流": "traffic flow", "时间序列": "time series", "时序": "time series",
        "道路交通": "road traffic",
        "缺失数据修复": "data imputation", "缺失修复": "data imputation",
        "缺失数据": "missing data", "缺失值插补": "data imputation",
        "数据修复": "data imputation", "图像修复": "image restoration",
        "缺失值": "missing values", "数据补全": "data completion",
        "图像": "image", "传感器": "sensor", "预测": "prediction",
        "修复": "repair", "插补": "imputation", "填补": "imputation",
        "缺失": "missing", "分类": "classification", "检测": "detection",
    }

    @classmethod
    def _translate_to_english(cls, query: str) -> str:
        """Translate known phrases without silently discarding unknown subjects."""
        import re
        # 检测是否有中文字符
        has_cjk = any('一' <= c <= '鿿' for c in query)
        if not has_cjk:
            return query

        # One pass avoids replacing 神经网络 before 图神经网络, and spaces
        # prevent adjacent replacements from becoming "flowdatarepair".
        terms = "|".join(re.escape(term) for term in sorted(cls._ZH_EN_MAP, key=len, reverse=True))
        result = re.sub(terms, lambda match: f" {cls._ZH_EN_MAP[match.group()]} ", query)
        return " ".join(result.split())

    _TASK_SYNONYMS = {
        "imputation": (
            "imputation",
            "imputing",
            "data completion",
            "data reconstruction",
            "data recovery",
        ),
        "prediction": ("prediction", "forecasting"),
        "classification": ("classification", "classifying"),
        "detection": ("detection", "detecting"),
    }
    _LANGUAGE_MODELS = (
        "large language model",
        "large language models",
        "pretrained language model",
        "pretrained language models",
        "pre-trained language model",
        "pre-trained language models",
        "LLM",
        "LLMs",
        "PLM",
        "PLMs",
    )

    @staticmethod
    def positive_query(query: str) -> str:
        """Remove question scaffolding, not unknown subject names or datasets."""
        text = re.sub(
            r"(?:而不是|而非|不是)\s*(?:只|仅)\s*(?:做|进行)?\s*(?:预测|分类|检测)"
            r"|\b(?:not|rather than)\s+(?:just|only)\s+"
            r"(?:prediction|forecasting|classification|detection)\b",
            " ",
            query,
            flags=re.I,
        )
        text = re.sub(
            r"\b(?:19|20)\d{2}\s*(?:年?\s*[-–—~至到]\s*(?:(?:19|20)\d{2}\s*年?|今)|年以来|年至今)",
            " ",
            text,
        )
        text = re.sub(r'\bNOT\s+(?:"[^"\n]{1,100}"|[A-Za-z][A-Za-z0-9_-]*)', " ", text)
        text = re.sub(r"(?:近|最近|过去)\s*[一二三四五六七八九十\d]+\s*年", " ", text)
        text = re.sub(
            r"(?:请帮我|帮我|我想查找|我想了解|查找|寻找|相关的?论文|的研究|下的|中的使用|中的|基于|用于|使用)",
            " ",
            text,
        )
        text = re.sub(
            r"\b(?:find|please|papers?|studies|research|using|used|recent|latest)\b",
            " ",
            text,
            flags=re.I,
        )
        return " ".join(text.split())

    @staticmethod
    def excluded_terms(query: str) -> list[str]:
        """Honor simple, explicit user NOT operands; 'not prediction-only' is not one."""
        return [
            quoted or word
            for quoted, word in re.findall(
                r'\bNOT\s+(?:"([^"\n]{1,100})"|([A-Za-z][A-Za-z0-9_-]*))',
                query,
            )
        ]

    @classmethod
    def task_evidence(cls, text: str, task: str) -> bool:
        normalized = cls._translate_to_english(text).casefold()
        if task == "imputation":
            return bool(
                re.search(
                    r"\b(?:imput(?:ation|ing|e)|data (?:completion|reconstruction|recovery))\b",
                    normalized,
                )
                or (
                    re.search(r"\bmissing\b", normalized)
                    and re.search(
                        r"\b(?:repair|reconstruct|recover|fill|complet)\w*\b",
                        normalized,
                    )
                )
            )
        return any(
            re.search(r"\b" + re.escape(term) + r"\b", normalized)
            for term in cls._TASK_SYNONYMS.get(task, ())
        )

    @classmethod
    def search_facets(cls, query: str) -> dict:
        """Small lexical facets; a match is metadata evidence, not a full-paper verdict."""
        text = cls._translate_to_english(cls.positive_query(query)).casefold()
        task = next((name for name in cls._TASK_SYNONYMS if cls.task_evidence(text, name)), "")
        method = bool(
            re.search(
                r"\b(?:large language models?|pre-?trained language models?|llms?|plms?)\b", text
            )
        )
        domain = re.sub(
            r"\b(?:large language models?|pre-?trained language models?|llms?|plms?)\b", " ", text
        )
        for terms in cls._TASK_SYNONYMS.values():
            for term in terms:
                domain = re.sub(r"\b" + re.escape(term) + r"\b", " ", domain)
        if task == "imputation":
            domain = re.sub(r"\b(?:recovery|reconstruction|completion)\b", " ", domain)
        domain = re.sub(
            r"\b(?:data|model|models|method|methods|missing|values?|repair|pretrained|pre|trained)\b",
            " ",
            domain,
        )
        domain = re.sub(r"\b(?:not|and|or)\b", " ", domain)
        return {"domain": cls._extract_keywords_rule(domain), "task": task, "method": method}

    @classmethod
    def _openalex_query(cls, query: str, *, original: str | None = None) -> str:
        # Explicit user Boolean syntax remains the user's intent. A model may
        # not turn 'not prediction-only' into NOT prediction for an entire PDF.
        if re.search(r"\b(?:AND|OR|NOT)\b", original if original is not None else query):
            return (original if original is not None else query).strip()
        original_facets = cls.search_facets(original) if original is not None else None
        if original_facets and original_facets["task"] == "imputation":
            query = re.sub(
                r"\bNOT\s+\(?\s*(?:prediction|forecasting)\s*\)?", " ", query, flags=re.I
            )
        if original_facets and (original_facets["task"] or original_facets["method"]):
            facets = original_facets
        elif original and re.search(r"\b(?:AND|OR|NOT)\b", query):
            return query.strip()  # preserve AI groups rather than AND-ing their synonyms
        else:
            facets = cls.search_facets(query)
        if not facets["task"] and not facets["method"]:
            return " ".join(
                cls._ZH_EN_MAP.get(kw, kw) for kw in cls._extract_keywords_rule(query)[:10]
            )
        parts = [
            "(traffic OR transportation OR road)"
            if term == "traffic"
            else "(flow OR speed OR volume)"
            if term == "flow" and "traffic" in facets["domain"]
            else term
            for term in facets["domain"][:8]
            if not (term == "road" and "traffic" in facets["domain"])
        ]
        if facets["task"]:
            parts.append(
                "("
                + " OR ".join('"' + term + '"' for term in cls._TASK_SYNONYMS[facets["task"]])
                + ")"
            )
        if facets["method"]:
            parts.append("(" + " OR ".join('"' + term + '"' for term in cls._LANGUAGE_MODELS) + ")")
        return " AND ".join(parts)

    @classmethod
    def ranking_query(cls, plan: QueryParseResult) -> str:
        return (
            plan.original_query
            if plan.intent == "exact_lookup"
            or re.search(r"\b(?:AND|OR|NOT)\b", plan.original_query)
            else cls._translate_to_english(cls.positive_query(plan.original_query))
        )

    @classmethod
    def resolve_paper_alias(cls, query: str) -> str:
        """Expand common model/paper nicknames into their canonical titles."""
        import re

        normalized = re.sub(
            r"[^a-z0-9]+",
            "",
            re.sub(
                r"^\s*(?:the|a|an)\s+|\s+paper\s*$",
                "",
                query.casefold().strip(),
            ),
        )
        return cls._PAPER_ALIASES.get(normalized, query)

    @classmethod
    def is_confident_single_paper_lookup(cls, query: str) -> bool:
        """Return whether an exact query identifies one paper unambiguously."""
        import re

        if cls.resolve_paper_alias(query) != query:
            return True
        lowered = query.casefold().strip()
        return bool(
            re.match(
                r"^.+?\s+by\s+[a-z][a-z\-']+(?:\s+et\s+al\.?)?$",
                lowered,
            )
            or re.match(
                r"^the\s+.+?\s+[a-z][a-z0-9_-]*\d{4}[a-z0-9_-]*\s+paper$",
                lowered,
            )
            or re.match(r"^(?:the|a)\s+paper\s+about\s+.+$", lowered)
            or (
                lowered.endswith(" paper")
                and len(
                    re.findall(
                        r"[a-z0-9]+",
                        re.sub(r"^(?:the|a)\s+|\s+paper$", "", lowered),
                    )
                )
                >= 2
            )
        )

    @classmethod
    def _optimize_query_for_source(
        cls,
        query: str,
        keywords: List[str],
        source: DataSource,
    ) -> str:
        """为特定数据源优化查询字符串"""
        import re

        # Asta 等数据集常用 “the <alias> <bibkey> paper” 指代一篇论文。
        # BibTeX key 不是论文正文词，加入 AND 查询反而会导致零召回。
        named_match = re.match(
            r"^\s*the\s+(.+?)\s+[a-z][a-z0-9_-]*\d{4}[a-z0-9_-]*\s+paper\s*$",
            query,
            flags=re.IGNORECASE,
        )
        if named_match:
            alias = named_match.group(1)
            alias = alias.replace("²", "2")
            alias = re.sub(r"([A-Za-z])\s*\^\s*(\d)", r"\1\2", alias)
            alias = re.sub(r"[-–—]+", " ", alias)
            alias = re.sub(r"\s+", " ", alias).strip()
            canonical = cls.resolve_paper_alias(f"the {alias} paper")
            return canonical if canonical != f"the {alias} paper" else alias

        expanded_alias = cls.resolve_paper_alias(query)
        if expanded_alias != query:
            return expanded_alias

        if source == DataSource.SEMANTIC_SCHOLAR:
            # S2 search is sensitive to punctuation-heavy natural questions.
            # A compact facet-preserving query avoids zero-result parser cases.
            compact = " ".join(keywords[:8]) if keywords else query
            return re.sub(r"[-–—]+", " ", compact)
        elif source == DataSource.OPENALEX:
            return cls._openalex_query(query)
        elif source == DataSource.CROSSREF:
            # CrossRef 对紧凑的高信息密度 bibliographic query 更稳定。
            translated = cls._translate_to_english(query)
            compact = cls._extract_keywords_rule(translated)
            return " ".join(compact[:10]) if compact else translated
        elif source == DataSource.ARXIV:
            en_keywords = [cls._ZH_EN_MAP.get(kw, kw) for kw in keywords]
            return " ".join(en_keywords[:10]) if en_keywords else cls._translate_to_english(query)
        return query

    @staticmethod
    def _generate_rationale(source: DataSource, intent: str) -> str:
        """为子查询生成理由"""
        rationales = {
            DataSource.SEMANTIC_SCHOLAR: "Semantic Scholar provides comprehensive citation data and relevance ranking",
            DataSource.OPENALEX: "OpenAlex offers broad open-access coverage and rich metadata",
            DataSource.CROSSREF: "CrossRef provides authoritative DOI-based metadata",
            DataSource.ARXIV: "arXiv has the latest preprints and open-access papers",
            DataSource.ZOTERO: "Zotero searches the user's imported local research library without a network request",
        }
        base = rationales.get(source, f"Search {source.value}")
        if intent == "exact_lookup":
            return f"{base}, optimized for precise matching"
        elif intent == "literature_review":
            return f"{base}, optimized for comprehensive coverage"
        return base

    @staticmethod
    def _generate_strategy(intent: str, sources: List[DataSource]) -> str:
        """生成检索策略描述"""
        source_names = [s.value for s in sources]
        strategies = {
            "exact_lookup": f"Precise lookup across {', '.join(source_names)} with strict matching criteria",
            "open_exploration": f"Broad exploration across {', '.join(source_names)} for comprehensive coverage",
            "literature_review": f"Systematic search across {', '.join(source_names)} for literature review",
            "similar_recommendation": f"Similar paper discovery across {', '.join(source_names)}",
            "methodology_survey": f"Methodology-focused search across {', '.join(source_names)}",
        }
        return strategies.get(intent, f"General search across {', '.join(source_names)}")
