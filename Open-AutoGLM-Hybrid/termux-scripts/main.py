"""
Open-AutoGLM 混合方案 - 主入口
集成手机控制器 + AI 模型调用，实现自动化操作闭环
"""

import argparse
import logging
import sys
import time
import traceback

from config import load_config, AppConfig
from ai_client import create_ai_client, Action, BaseAIClient
from phone_controller import PhoneController

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger('AutoGLM')

MAX_STEPS = 20
STEP_INTERVAL = 1.5


def _denormalize_coord(val: int, screen_size: int) -> int:
    """将 0-999 归一化坐标转换为像素坐标"""
    return int(val / 1000 * screen_size)


def execute_action(action: Action, phone: PhoneController,
                   screen_size: tuple[int, int] = None) -> tuple[bool, str]:
    """
    执行一个动作，返回 (是否成功, 描述信息)
    screen_size: (width, height) 用于归一化坐标转换
    """
    p = action.params
    reason = p.get('reason', '')
    sw, sh = screen_size or (1080, 2340)

    try:
        if action.action_type == Action.TYPE_LAUNCH_APP:
            name = p.get('app_name', '')
            pkg = p.get('package_name', '')
            label = name or pkg
            ok = phone.launch_app(app_name=name, package_name=pkg)
            time.sleep(2)
            return ok, f"打开应用 \"{label}\"  {reason}"

        elif action.action_type in (Action.TYPE_TAP, Action.TYPE_LONG_PRESS,
                                     Action.TYPE_DOUBLE_TAP):
            x, y = int(p['x']), int(p['y'])
            if action.normalized_coords:
                x = _denormalize_coord(x, sw)
                y = _denormalize_coord(y, sh)
            ok = phone.tap(x, y)
            label = action.action_type.replace('_', ' ')
            return ok, f"{label} ({x}, {y})  {reason}"

        elif action.action_type == Action.TYPE_SWIPE:
            x1, y1 = int(p['x1']), int(p['y1'])
            x2, y2 = int(p['x2']), int(p['y2'])
            if action.normalized_coords:
                x1 = _denormalize_coord(x1, sw)
                y1 = _denormalize_coord(y1, sh)
                x2 = _denormalize_coord(x2, sw)
                y2 = _denormalize_coord(y2, sh)
            ok = phone.swipe(x1, y1, x2, y2)
            return ok, f"滑动 ({x1},{y1})->({x2},{y2})  {reason}"

        elif action.action_type == Action.TYPE_INPUT:
            text = str(p.get('text', ''))
            ok = phone.input_text(text)
            return ok, f"输入 \"{text}\"  {reason}"

        elif action.action_type == Action.TYPE_BACK:
            ok = phone.back()
            return ok, f"返回  {reason}"

        elif action.action_type == Action.TYPE_HOME:
            ok = phone.home()
            return ok, f"回到桌面  {reason}"

        elif action.action_type == Action.TYPE_WAIT:
            secs = int(p.get('seconds', 2))
            time.sleep(secs)
            return True, f"等待 {secs}s  {reason}"

        elif action.action_type == Action.TYPE_DONE:
            return True, f"任务完成  {reason}"

        elif action.action_type == Action.TYPE_TAKE_OVER:
            return False, f"需要人工接管: {reason}"

        else:
            return False, f"未知动作: {action}"

    except (KeyError, ValueError, TypeError) as e:
        return False, f"动作参数错误: {e}, action={action}"


def run_task(task: str, phone: PhoneController, ai: BaseAIClient,
             show_thinking: bool = False):
    """执行一个自动化任务"""
    print(f"[任务] {task}\n")

    history: list = []

    for step in range(1, MAX_STEPS + 1):
        print(f"--- 步骤 {step}/{MAX_STEPS} ---")

        screenshot = phone.screenshot()
        if screenshot is None:
            print("  截图失败，2s 后重试...")
            time.sleep(2)
            history.append({
                "role": "user",
                "content": "（截图失败，请根据上一步的信息判断下一步操作）",
            })
            continue

        screen_size = screenshot.size  # (width, height)

        action, thinking = ai.decide_action(task, screenshot, history)

        if show_thinking and thinking:
            print(f"  [思考] {thinking[:500]}")
            if len(thinking) > 500:
                print(f"         ...（共 {len(thinking)} 字）")

        ok, desc = execute_action(action, phone, screen_size=screen_size)
        status = "成功" if ok else "失败"
        print(f"  => {desc}  [{status}]")

        # history: 记录这一轮的 assistant 回复和执行结果
        history.append({
            "role": "assistant",
            "content": str(action),
        })
        history.append({
            "role": "user",
            "content": f"上一步操作「{desc}」执行{status}。请根据新截图决定下一步。",
        })

        if action.action_type == Action.TYPE_DONE:
            print(f"\n任务完成！")
            return

        if action.action_type == Action.TYPE_UNKNOWN:
            print(f"  模型返回了无法识别的动作，跳过")

        time.sleep(STEP_INTERVAL)

    print(f"\n已达到最大步数 ({MAX_STEPS})，任务结束。")


def interactive_mode(config_path: str = None, verbose: bool = False,
                     mode_override: str = None):
    """交互式模式"""
    print("\n╔══════════════════════════════════════╗")
    print("║     Open-AutoGLM 混合方案 v1.0      ║")
    print("║  支持: OpenAI 兼容 / 智谱 AI        ║")
    print("╚══════════════════════════════════════╝\n")

    cfg = load_config(config_path)
    mode = mode_override or cfg.helper.mode

    thinking_label = " + 思考模式" if cfg.ai.thinking else ""
    print(f"  AI 服务: {cfg.ai.provider} / {cfg.ai.model}{thinking_label}")
    print(f"  控制地址: {cfg.helper.url}")

    ai = create_ai_client(cfg.ai)
    phone = PhoneController(helper_url=cfg.helper.url, preferred_mode=mode)
    print(f"  控制模式: {phone.get_mode()}\n")

    while True:
        try:
            task = input("请输入任务 (输入 q 退出): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not task:
            continue
        if task.lower() in ('q', 'quit', 'exit'):
            print("再见！")
            break

        try:
            run_task(task, phone, ai, show_thinking=cfg.ai.thinking)
        except KeyboardInterrupt:
            print("\n任务被中断。")
        except Exception as e:
            logger.error(f"任务执行失败: {e}")
            if verbose:
                traceback.print_exc()

        print()


def main():
    parser = argparse.ArgumentParser(description='Open-AutoGLM 混合方案')
    parser.add_argument('task', nargs='?',
                        help='要执行的任务（省略则进入交互模式）')
    parser.add_argument('-c', '--config', help='配置文件路径', default=None)
    parser.add_argument('-m', '--mode', choices=['auto', 'accessibility', 'ladb'],
                        default=None,
                        help='控制模式: auto=自动检测, accessibility=仅无障碍, ladb=仅ADB')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细日志')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.task:
        cfg = load_config(args.config)
        mode = args.mode or cfg.helper.mode
        ai = create_ai_client(cfg.ai)
        phone = PhoneController(helper_url=cfg.helper.url, preferred_mode=mode)
        run_task(args.task, phone, ai, show_thinking=cfg.ai.thinking)
    else:
        interactive_mode(args.config, args.verbose, mode_override=args.mode)


if __name__ == '__main__':
    main()
