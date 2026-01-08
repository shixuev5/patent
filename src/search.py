import json
from typing import Dict, Any, List
from loguru import logger
from src.llm import get_llm_service

class SearchStrategyGenerator:
    """
    专利审查检索策略生成器
    
    功能：
    1. 结合 G01M 审查指南和查询语法，制定分步检索策略。
    2. 针对 IPC/CPC、关键词、结构关系、非专利文献生成多维度的检索表达式。
    3. 支持 Few-Shot 学习，利用指南中的案例作为范本。
    """
    
    def __init__(self, report_json: Dict[str, Any], patent_data: Dict[str, Any]):
        self.llm_service = get_llm_service()
        self.report = report_json
        self.biblio = patent_data
        
        # 提取关键字段
        self.title = self.biblio.get("invention_title", "")
        self.abstract = self.biblio.get("abstract", "")
        self.applicants = [a.get("name", "") for a in self.biblio.get("applicants", [])]
        self.ipcs = self.biblio.get("ipc_classifications", [])
        
        # 提取分析报告中的深层理解
        self.problem = self.report.get("technical_problem", "")
        self.means = self.report.get("technical_means", "")
        self.features = self.report.get("technical_features", [])

    def generate_search_plan(self) -> Dict[str, Any]:
        """执行生成流程"""
        logger.info(f"开始生成检索策略: {self.title}")
        
        try:
            # 1. 构建包含 Few-Shot 案例的 System Prompt
            system_prompt = self._build_system_prompt()
            
            # 2. 构建包含当前专利具体信息的 User Prompt
            user_prompt = self._build_user_prompt()
            
            # 3. 调用 LLM
            response = self.llm_service.chat_completion_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3  # 保持一定的创造性以生成多样的关键词
            )
            
            # 4. 补充元数据
            response["meta"] = {
                "target_patent": self.title,
                "applicant_type": "University/Institute" if self._check_is_university() else "Company",
                "strategy_basis": "G01M Guide + Standard Query Syntax"
            }
            
            logger.success("检索策略生成完成")
            return response

        except Exception as e:
            logger.error(f"生成检索策略失败: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def _check_is_university(self) -> bool:
        """简单的规则判断是否为高校或科研院所"""
        keywords = ["大学", "学院", "研究所", "研究院", "University", "Institute"]
        for app in self.applicants:
            if any(k in app for k in keywords):
                return True
        return False

    def _build_system_prompt(self) -> str:
        return """
        你是一位精通 G01M 领域（测试与测量）的专利审查员检索专家。
        你的任务是制定专业的检索策略，并编写符合标准通用检索语法（Common Query Syntax）的检索表达式。

        ### 核心指引 (Guidelines)
        1. **数据库选择**：
           - 若申请人是**高校/研究所**，或涉及**算法/模型/标准**：必须包含非专利库（NPL, 如 CNKI/IEEE）的检索步骤。
           - 若涉及具体机械结构：重点在专利库（CNTXT/DWPI）。
        
        2. **G01M 专用策略**：
           - **分类号优先**：对于 G01M3 (密封)、G01M13 (机器部件)、G01M1 (平衡) 等准确分类，使用 `IPC/CPC AND 关键词`。
           - **语义/功能改写**：对于分类不准（如 G01M99）或应用跑偏（如电池测试分入 H01M），不要依赖分类号，而是提取“测试原理”或“具体应用场景”进行文本改写检索。
           - **交叉领域**：涉及图像处理加 G06T，涉及材料测试加 G01N。

        3. **查询语法规范 (Syntax Rules)**：
           - **截词**：`+` (任意多字符, electric+), `?` (0/1字符, colo?r), `#` (正好1个)。
           - **逻辑**：`AND`, `OR`, `NOT`, `XOR`。
           - **邻近算符**：
             - `nW`: 顺序固定 (Wind 1W Turbine)。
             - `nD`: 顺序不限 (Sensor 5D Temperature)。
             - `S` / `P`: 同句 / 同段。
           - **字段**：`/TI`, `/AB`, `/CL` (权利要求), `/IC`, `/CC` (CPC)。
           - **范围**：`:` (2010:2025)。

        ### Few-Shot Examples (学习案例)

        **案例 1：涉及算法的轴承故障诊断**
        *输入*：基于卷积神经网络(CNN)的风机轴承故障检测，申请人：XX大学。
        *思考*：高校申请，涉及算法，G01M13与G06交叉。
        *输出策略*：
        - Step 1 (Broad): G01M13/045 (声学诊断) AND (Neural Network OR CNN OR Deep Learning).
        - Step 2 (NPL): 在 IEEE/CNKI 中检索 "Bearing fault diagnosis" AND "Convolutional Neural Network".
        - Step 3 (Semantic): 检索式构造为 `(BEARING S FAULT S DIAGNOS+) AND (IMAGE 3D PROCESS+)`.

        **案例 2：锂电池密封性检测**
        *输入*：一种利用无水硫酸铜变色原理检测软包电池密封性的方法。
        *思考*：可能被分入 H01M (电池)，分类号不准。原理特殊。
        *输出策略*：
        - Step 1 (Semantic): 忽略分类号，直接检索原理关键词。Query: `((ANHYDROUS 1W COPPER 1W SULFATE) OR (CUSO4)) AND (SEAL+ OR LEAK+)`.
        - Step 2 (Cross-Domain): 联合 G01M3/00 (密封测试) AND H01M (电池).

        ### 输出格式要求 (JSON)
        请输出 JSON，包含：
        1. `analysis`: 简短分析（申请人类型、技术难点、分类号精准度预判）。
        2. `keywords`: { "en": [], "zh": [], "expansion": [] } (中英文关键词及同义词扩展)。
        3. `search_steps`: 列表，每个步骤包含：
           - `type`: 策略类型 (Classification / Structural / Semantic / NPL / Cross-Domain)。
           - `objective`: 该步骤的目的。
           - `databases`: 推荐数据库 (如 CNTXT, DWPI, IEEE)。
           - `queries`: [ "表达式1", "表达式2" ] (**关键：每个步骤至少给出 3 个不同构造的表达式**)。
        """

    def _build_user_prompt(self) -> str:
        # 格式化特征列表
        features_str = "\n".join([f"- {f['name']}: {f['description']}" for f in self.features])
        
        prompt = f"""
        # 待检索专利详情
        **标题**: {self.title}
        **申请人**: {', '.join(self.applicants)}
        **当前 IPC**: {', '.join(self.ipcs)}
        
        **摘要**: {self.abstract}
        
        **核心技术问题**: {self.problem}
        **技术手段**: {self.means}
        **关键特征**: 
        {features_str}

        # 任务指令
        请根据上述信息，生成一套完整的检索策略。
        特别注意：
        1. 如果申请人是高校，必须生成 **NPL (非专利文献)** 检索步骤。
        2. 针对 "{self.title}" 中的具体结构（如传感器位置、连接方式），请使用 **邻近算符 (nW/nD)** 编写精确检索式。
        3. 考虑到 IPC {self.ipcs}，请判断其准确性，如果太宽泛，请在策略中体现 CPC 或关键词限定。
        4. 检索式请主要以 **英文 (通用语法)** 为主，适配 DWPI/VEN 等国际库，必要时提供中文关键词组合适配 CNTXT。
        """
        return prompt