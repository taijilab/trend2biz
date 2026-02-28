from __future__ import annotations

from typing import Optional


# Priority-ordered rules — first match wins.
# Each rule defines the full biz profile for that category.
_RULES = [
    {
        "category": "biotech",
        "keywords": [
            "biotech", "bioinformatics", "genomics", "proteomics", "crispr",
            "drug discovery", "clinical trial", "molecular", "protein folding",
            "medical imaging", "pathology", "sequencing", "biopharma",
        ],
        "scenarios": ["生命科学研究", "药物研发"],
        "persona": "Research",
        "buyer": "生命科学研究机构 / 制药企业",
        "delivery_forms": ["OSS", "SDK"],
        "monetization": ["科研授权", "企业定制服务"],
        "sales_motion": "Enterprise",
        "market_base": 6.5,
    },
    {
        "category": "fintech",
        "keywords": [
            "fintech", "trading", "quant", "portfolio", "payment", "wallet",
            "defi", "crypto", "blockchain", "exchange", "lending", "kyc",
            "fraud detection", "risk management", "hedge fund", "order book",
        ],
        "scenarios": ["金融分析", "量化交易", "支付处理"],
        "persona": "Finance",
        "buyer": "金融机构 CTO / 量化团队",
        "delivery_forms": ["SaaS", "On-premise"],
        "monetization": ["数据订阅", "SaaS", "API 按量计费"],
        "sales_motion": "Enterprise",
        "market_base": 6.5,
    },
    {
        "category": "robotics-iot",
        "keywords": [
            "robot", "robotics", "ros", "drone", "autonomous vehicle", "embedded",
            "iot", "firmware", "sensor", "actuator", "slam", "lidar",
            "edge computing", "real-time os", "rtos",
        ],
        "scenarios": ["机器人控制", "物联网设备管理", "边缘计算"],
        "persona": "Hardware",
        "buyer": "硬件研发团队 / 制造企业",
        "delivery_forms": ["SDK", "On-premise"],
        "monetization": ["硬件捆绑", "On-premise 授权"],
        "sales_motion": "Enterprise",
        "market_base": 6.3,
    },
    {
        "category": "edu-tech",
        "keywords": [
            "education", "learning", "tutorial", "course", "quiz", "lms",
            "student", "teach", "school", "university", "curriculum", "e-learning",
            "coding challenge", "assessment",
        ],
        "scenarios": ["在线教育", "学习管理", "技能评估"],
        "persona": "Edu",
        "buyer": "教育机构 / 个人学习者",
        "delivery_forms": ["SaaS", "OSS"],
        "monetization": ["订阅制", "机构授权"],
        "sales_motion": "PLG",
        "market_base": 5.8,
    },
    {
        "category": "agent",
        "keywords": [
            "agent", "rag", "llm", "gpt", "copilot", "ai assistant", "chatbot",
            "multi-agent", "langchain", "autogen", "workflow automation",
            "openai", "claude", "gemini", "mistral", "vector db",
            "embedding", "fine-tun", "machine learning", "deep learning",
            "neural network", "transformer", "diffusion",
        ],
        "scenarios": ["智能自动化", "AI 助手", "内容生产"],
        "persona": "Data",
        "buyer": "AI 产品负责人 / 数据工程团队",
        "delivery_forms": ["OSS", "SaaS", "SDK"],
        "monetization": ["API 调用计费", "Open-core", "SaaS 订阅"],
        "sales_motion": "PLG",
        "market_base": 6.8,
    },
    {
        "category": "security",
        "keywords": [
            "security", "vuln", "pentest", "siem", "soc", "devsecops",
            "zero trust", "intrusion", "malware", "exploit", "cve", "compliance",
            "audit", "secret scanning", "threat", "firewall", "encryption",
            "identity", "rbac", "ssrf", "xss",
        ],
        "scenarios": ["安全防护", "风险检测", "合规审计"],
        "persona": "Sec",
        "buyer": "安全团队 / CISO",
        "delivery_forms": ["SaaS", "On-premise"],
        "monetization": ["SaaS 订阅", "企业授权"],
        "sales_motion": "Enterprise",
        "market_base": 6.5,
    },
    {
        "category": "data-platform",
        "keywords": [
            "data pipeline", "etl", "data warehouse", "lakehouse", "spark",
            "flink", "kafka", "dbt", "airflow", "dataflow", "stream processing",
            "batch processing", "data quality", "data catalog", "iceberg",
            "delta lake", "data integration",
        ],
        "scenarios": ["数据集成", "流式处理", "数据仓库"],
        "persona": "Data",
        "buyer": "数据工程负责人",
        "delivery_forms": ["OSS", "SaaS", "On-premise"],
        "monetization": ["Open-core", "云托管服务"],
        "sales_motion": "PLG",
        "market_base": 6.2,
    },
    {
        "category": "observability",
        "keywords": [
            "observability", "tracing", "monitor", "prometheus", "grafana",
            "opentelemetry", "apm", "distributed tracing", "alerting",
            "slo", "sre", "log aggregation", "dashboarding",
        ],
        "scenarios": ["可观测性", "性能监控", "告警管理"],
        "persona": "Infra",
        "buyer": "平台工程 / SRE 团队",
        "delivery_forms": ["OSS", "SaaS"],
        "monetization": ["Open-core", "云托管"],
        "sales_motion": "PLG",
        "market_base": 6.0,
    },
    {
        "category": "media-tech",
        "keywords": [
            "video", "audio", "media", "streaming", "codec", "image processing",
            "ffmpeg", "podcast", "broadcast", "content creation", "editing",
            "transcoding", "subtitle", "speech recognition",
        ],
        "scenarios": ["媒体处理", "内容创作工具"],
        "persona": "Creator",
        "buyer": "内容创作者 / 媒体公司",
        "delivery_forms": ["SDK", "SaaS"],
        "monetization": ["SaaS 订阅", "API 按量计费"],
        "sales_motion": "PLG",
        "market_base": 5.8,
    },
    {
        "category": "low-code",
        "keywords": [
            "low-code", "no-code", "visual builder", "drag-and-drop",
            "workflow builder", "form builder", "app builder",
            "automation platform", "n8n", "make", "zapier",
        ],
        "scenarios": ["低代码应用构建", "业务流程自动化"],
        "persona": "Ops",
        "buyer": "业务运营 / 中小企业主",
        "delivery_forms": ["SaaS", "OSS"],
        "monetization": ["SaaS 订阅", "Open-core"],
        "sales_motion": "PLG",
        "market_base": 6.0,
    },
    {
        "category": "enterprise-saas",
        "keywords": [
            "crm", "erp", "hrm", "helpdesk", "ticketing", "project management",
            "collaboration", "b2b saas", "workspace", "customer success",
            "account management", "sales pipeline",
        ],
        "scenarios": ["企业协作", "客户管理", "项目管理"],
        "persona": "Ops",
        "buyer": "企业运营决策者",
        "delivery_forms": ["SaaS", "On-premise"],
        "monetization": ["SaaS 订阅", "企业席位授权"],
        "sales_motion": "Enterprise",
        "market_base": 5.8,
    },
    {
        "category": "infra",
        "keywords": [
            "kubernetes", "k8s", "container", "docker", "helm", "terraform",
            "ansible", "cloud native", "service mesh", "istio", "envoy",
            "nginx", "load balancer", "dns", "vpn", "proxy", "gateway",
            "pulumi", "cdk",
        ],
        "scenarios": ["基础设施管理", "容器编排", "网络代理"],
        "persona": "Infra",
        "buyer": "平台工程 / DevOps 团队",
        "delivery_forms": ["OSS", "On-premise"],
        "monetization": ["Open-core", "云服务"],
        "sales_motion": "PLG",
        "market_base": 5.8,
    },
    {
        "category": "devops",
        "keywords": [
            "ci/cd", "cicd", "pipeline", "github actions", "gitlab ci",
            "jenkins", "deployment", "release", "gitops", "argocd", "fluxcd",
            "e2e testing", "test automation", "performance testing",
        ],
        "scenarios": ["持续集成", "自动化部署", "质量保障"],
        "persona": "DevOps",
        "buyer": "工程效能 / DevOps 团队",
        "delivery_forms": ["OSS", "SaaS"],
        "monetization": ["Open-core", "SaaS"],
        "sales_motion": "PLG",
        "market_base": 5.5,
    },
    {
        "category": "devtools",
        "keywords": [
            "cli tool", "sdk", "library", "framework", "ide plugin", "linter",
            "formatter", "debugger", "profiler", "code generation", "scaffolding",
            "package manager", "build tool", "compiler",
        ],
        "scenarios": ["开发工具链", "代码生成", "调试效率"],
        "persona": "Dev",
        "buyer": "研发工程师",
        "delivery_forms": ["OSS", "SDK"],
        "monetization": ["Open-core", "商业 IDE 插件"],
        "sales_motion": "PLG",
        "market_base": 5.2,
    },
]

_DEFAULT_RULE = {
    "category": "developer-tools",
    "scenarios": ["通用研发效率"],
    "persona": "Dev",
    "buyer": "研发工程师",
    "delivery_forms": ["OSS"],
    "monetization": ["Open-core", "SaaS"],
    "sales_motion": "PLG",
    "market_base": 5.0,
}


def infer_biz_profile(repo_name: str, description: Optional[str], language: Optional[str]) -> dict:
    text = f"{repo_name} {description or ''}".lower()

    matched = _DEFAULT_RULE
    for rule in _RULES:
        if any(k in text for k in rule["keywords"]):
            matched = rule
            break

    return {
        "category": matched["category"],
        "user_persona": matched["persona"],
        "scenarios": matched["scenarios"],
        "value_props": ["效率提升", "降本增效"],
        "delivery_forms": list(matched["delivery_forms"]),
        "monetization_candidates": list(matched["monetization"]),
        "buyer": matched["buyer"],
        "sales_motion": matched.get("sales_motion", "PLG"),
        "confidence": 0.65,
        "explanations": {
            "method": "rule-v1",
            "signals": [repo_name, description or ""],
            "market_base": matched.get("market_base", 5.0),
        },
    }
