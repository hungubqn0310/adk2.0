from typing import List, Optional
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from typing_extensions import override
from google.adk.planners.plan_re_act_planner import (
    PlanReActPlanner,
    PLANNING_TAG,
    REPLANNING_TAG,
    REASONING_TAG,
    ACTION_TAG,
    FINAL_ANSWER_TAG,
)


class CngPlaner(PlanReActPlanner):
    """
    Custom planner for the CNG agent that extends PlanReActPlanner.
    This planner is meant to prevent the model from generating python code when using tools.
    """

    @override
    def process_planning_response(
            self,
            callback_context: CallbackContext,
            response_parts: List[types.Part],
    ) -> Optional[List[types.Part]]:
        parts = super().process_planning_response(callback_context, response_parts)
        # remove empty string parts caused by incorrect splitting in core ADK code
        parts = [p for p in parts if not (p.text and not p.text.strip())]
        return parts

    def _build_nl_planner_instruction(self) -> str:
        """Builds the NL planner instruction for the Plan-Re-Act planner.

        Returns:
          NL planner system instruction.
        """

        high_level_preamble = f"""
When answering the question, try to leverage the available tools to gather the information instead of your memorized knowledge.

Follow this process when answering the question: (1) first come up with a plan in natural language text format **YOU MUST MAKE A PLAN BEFORE DOING ANYTHING ELSE**.; (2) Then use function calls to execute the plan and provide reasoning between function calls to make a summary of current state and next step. Function calls and reasoning should be interleaved with each other. (3) In the end, return one final answer.

Follow this format when answering the question: (1) The planning part should be under {PLANNING_TAG}. (2) The function calls should be under {ACTION_TAG}, and the reasoning parts should be under {REASONING_TAG}. (3) The final answer part should be under {FINAL_ANSWER_TAG}.
"""

        planning_preamble = f"""
Below are the requirements for the planning:
The plan is made to answer the user query if following the plan. The plan is coherent and covers all aspects of information from user query, and only involves the tools that are accessible by the agent. The plan contains the decomposed steps as a numbered list where each step should use one or multiple available tools. By reading the plan, you can intuitively know which tools to trigger or what actions to take.
If the initial plan cannot be successfully executed, you should learn from previous execution results and revise your plan. The revised plan should be be under {REPLANNING_TAG}. Then use tools to follow the new plan.
"""

        reasoning_preamble = """
Below are the requirements for the reasoning:
The reasoning makes a summary of the current trajectory based on the user query and tool outputs. Based on the tool outputs and plan, the reasoning also comes up with instructions to the next steps, making the trajectory closer to the final answer.
"""

        final_answer_preamble = """
Below are the requirements for the final answer:
The final answer MUST be a function call to the `set_model_response` tool. The final answer should be precise and follow query formatting requirements. Some queries may not be answerable with the available tools and information. In those cases, inform the user why you cannot process their query and ask for more information.
"""

        # Only contains the requirements for custom tool/libraries.
        tool_code_without_python_libraries_preamble = """
Below are the requirements for the tool code:

**Custom Tools:** The available tools are described in the context and can be directly used.
- NEVER write your own code other than the function calls using the provided tools.
- You cannot use any parameters or fields that are not explicitly defined in the APIs in the context.
"""

        user_input_preamble = """
VERY IMPORTANT instruction that you MUST follow in addition to the above instructions:

You should ask for clarification if you need more information to answer the question.
You should prefer using the information available in the context instead of repeated tool use.
"""

        return '\n\n'.join([
            high_level_preamble,
            planning_preamble,
            reasoning_preamble,
            final_answer_preamble,
            tool_code_without_python_libraries_preamble,
            user_input_preamble,
        ])
