"""
Open-AutoGLM 混合方案 - 手机控制器（自动降级逻辑）
版本: 1.0.0

支持两种控制模式:
1. 无障碍服务模式 (优先) - 通过 AutoGLM Helper APP
2. LADB 模式 (备用) - 通过 ADB 连接

自动检测可用模式并降级
"""

import os
import subprocess
import requests
import base64
import time
import logging
from typing import Optional, Tuple
from PIL import Image
from io import BytesIO

logger = logging.getLogger('PhoneController')


class PhoneController:
    """手机控制器 - 支持自动降级"""
    
    # 控制模式
    MODE_ACCESSIBILITY = "accessibility"  # 无障碍服务模式
    MODE_LADB = "ladb"  # LADB 模式
    MODE_NONE = "none"  # 无可用模式
    
    def __init__(self, helper_url: str = "http://localhost:6443",
                 preferred_mode: str = "auto"):
        """
        初始化手机控制器

        Args:
            helper_url: AutoGLM Helper 的 URL
            preferred_mode: 控制模式偏好
                - "auto": 自动检测（先无障碍，后 LADB）
                - "accessibility": 仅无障碍服务
                - "ladb": 仅 LADB/ADB
        """
        self.helper_url = helper_url
        self.mode = self.MODE_NONE
        self.adb_device = None
        self.preferred_mode = preferred_mode.lower()

        self._detect_mode()

    def _detect_mode(self):
        """根据 preferred_mode 检测并设置控制模式"""
        logger.info(f"检测控制模式 (偏好: {self.preferred_mode})...")

        if self.preferred_mode == "accessibility":
            if self._try_accessibility_service():
                self.mode = self.MODE_ACCESSIBILITY
                logger.info(f"✅ 使用无障碍服务模式 ({self.helper_url})")
                return
            raise Exception(
                "无障碍服务不可用！\n"
                "请确保 AutoGLM Helper 已运行并开启无障碍权限。\n"
                "如需自动降级，请将 mode 设为 auto。"
            )

        if self.preferred_mode == "ladb":
            if self._try_ladb():
                self.mode = self.MODE_LADB
                logger.info(f"✅ 使用 LADB 模式 (设备: {self.adb_device})")
                return
            raise Exception(
                "LADB/ADB 不可用！\n"
                "请确保 ADB 已安装且设备已连接。\n"
                "如需自动降级，请将 mode 设为 auto。"
            )

        # auto: 先无障碍，后 LADB
        if self._try_accessibility_service():
            self.mode = self.MODE_ACCESSIBILITY
            logger.info(f"✅ 使用无障碍服务模式 ({self.helper_url})")
            return

        if self._try_ladb():
            self.mode = self.MODE_LADB
            logger.warning(f"⚠️ 降级到 LADB 模式 (设备: {self.adb_device})")
            return

        self.mode = self.MODE_NONE
        logger.error("❌ 无可用控制方式")
        raise Exception(
            "无法连接到手机控制服务！\n"
            "请确保:\n"
            "1. AutoGLM Helper 已运行并开启无障碍权限\n"
            "2. 或者 LADB 已配对并运行\n"
        )
    
    def _try_accessibility_service(self) -> bool:
        """尝试连接无障碍服务"""
        try:
            response = requests.get(
                f"{self.helper_url}/status",
                timeout=3
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('accessibility_enabled'):
                    return True
                else:
                    logger.warning("AutoGLM Helper 运行中，但无障碍服务未开启")
                    return False
            
            return False
        except Exception as e:
            logger.debug(f"无障碍服务连接失败: {e}")
            return False
    
    def _try_ladb(self) -> bool:
        """尝试连接 LADB"""
        try:
            # 检查 adb 是否可用
            result = subprocess.run(
                ['adb', 'devices'],
                capture_output=True,
                text=True,
                timeout=3
            )
            
            if result.returncode != 0:
                logger.debug("ADB 命令不可用")
                return False
            
            # 解析设备列表
            lines = result.stdout.strip().split('\n')[1:]  # 跳过标题行
            devices = [line.split('\t')[0] for line in lines if '\tdevice' in line]
            
            if not devices:
                logger.debug("未找到已连接的 ADB 设备")
                return False
            
            # 使用第一个设备
            self.adb_device = devices[0]
            logger.info(f"找到 ADB 设备: {self.adb_device}")
            
            # 测试连接
            test_result = subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'echo', 'test'],
                capture_output=True,
                timeout=3
            )
            
            return test_result.returncode == 0
            
        except Exception as e:
            logger.debug(f"LADB 连接失败: {e}")
            return False
    
    def get_mode(self) -> str:
        """获取当前控制模式"""
        return self.mode
    
    def screenshot(self) -> Optional[Image.Image]:
        """
        截取屏幕
        
        Returns:
            PIL.Image 对象，失败返回 None
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._screenshot_accessibility()
        elif self.mode == self.MODE_LADB:
            return self._screenshot_ladb()
        else:
            logger.error("无可用的截图方式")
            return None
    
    def _screenshot_accessibility(self) -> Optional[Image.Image]:
        """通过无障碍服务截图"""
        try:
            response = requests.get(
                f"{self.helper_url}/screenshot",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    # 解码 Base64 图片
                    image_data = base64.b64decode(data['image'])
                    image = Image.open(BytesIO(image_data))
                    logger.debug(f"截图成功 (无障碍): {image.size}")
                    return image
            
            logger.error(f"截图失败: HTTP {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"截图失败 (无障碍): {e}")
            return None
    
    def _screenshot_ladb(self) -> Optional[Image.Image]:
        """通过 LADB 截图"""
        try:
            # 截图到设备
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'screencap', '-p', '/sdcard/autoglm_screenshot.png'],
                check=True,
                timeout=5
            )
            
            # 拉取到本地
            local_path = '/tmp/autoglm_screenshot.png'
            subprocess.run(
                ['adb', '-s', self.adb_device, 'pull', '/sdcard/autoglm_screenshot.png', local_path],
                check=True,
                timeout=5
            )
            
            # 打开图片
            image = Image.open(local_path)
            logger.debug(f"截图成功 (LADB): {image.size}")
            
            # 清理临时文件
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'rm', '/sdcard/autoglm_screenshot.png'],
                timeout=3
            )
            
            return image
            
        except Exception as e:
            logger.error(f"截图失败 (LADB): {e}")
            return None
    
    def tap(self, x: int, y: int) -> bool:
        """
        执行点击操作
        
        Args:
            x: X 坐标
            y: Y 坐标
        
        Returns:
            是否成功
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._tap_accessibility(x, y)
        elif self.mode == self.MODE_LADB:
            return self._tap_ladb(x, y)
        else:
            logger.error("无可用的点击方式")
            return False
    
    def _tap_accessibility(self, x: int, y: int) -> bool:
        """通过无障碍服务点击"""
        try:
            response = requests.post(
                f"{self.helper_url}/tap",
                json={'x': x, 'y': y},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                success = data.get('success', False)
                logger.debug(f"点击 ({x}, {y}): {success}")
                return success
            
            return False
            
        except Exception as e:
            logger.error(f"点击失败 (无障碍): {e}")
            return False
    
    def _tap_ladb(self, x: int, y: int) -> bool:
        """通过 LADB 点击"""
        try:
            result = subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'input', 'tap', str(x), str(y)],
                check=True,
                timeout=3
            )
            
            logger.debug(f"点击 ({x}, {y}): True")
            return True
            
        except Exception as e:
            logger.error(f"点击失败 (LADB): {e}")
            return False
    
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> bool:
        """
        执行滑动操作
        
        Args:
            x1: 起点 X 坐标
            y1: 起点 Y 坐标
            x2: 终点 X 坐标
            y2: 终点 Y 坐标
            duration: 持续时间 (毫秒)
        
        Returns:
            是否成功
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._swipe_accessibility(x1, y1, x2, y2, duration)
        elif self.mode == self.MODE_LADB:
            return self._swipe_ladb(x1, y1, x2, y2, duration)
        else:
            logger.error("无可用的滑动方式")
            return False
    
    def _swipe_accessibility(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        """通过无障碍服务滑动"""
        try:
            response = requests.post(
                f"{self.helper_url}/swipe",
                json={'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'duration': duration},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                success = data.get('success', False)
                logger.debug(f"滑动 ({x1},{y1}) -> ({x2},{y2}): {success}")
                return success
            
            return False
            
        except Exception as e:
            logger.error(f"滑动失败 (无障碍): {e}")
            return False
    
    def _swipe_ladb(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        """通过 LADB 滑动"""
        try:
            result = subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'input', 'swipe', 
                 str(x1), str(y1), str(x2), str(y2), str(duration)],
                check=True,
                timeout=5
            )
            
            logger.debug(f"滑动 ({x1},{y1}) -> ({x2},{y2}): True")
            return True
            
        except Exception as e:
            logger.error(f"滑动失败 (LADB): {e}")
            return False
    
    def back(self) -> bool:
        """执行返回操作"""
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._back_accessibility()
        elif self.mode == self.MODE_LADB:
            return self._back_ladb()
        else:
            logger.error("无可用的返回方式")
            return False

    def _back_accessibility(self) -> bool:
        try:
            response = requests.post(f"{self.helper_url}/back", timeout=5)
            if response.status_code == 200:
                return response.json().get('success', False)
            return False
        except Exception as e:
            logger.error(f"返回失败 (无障碍): {e}")
            return False

    def _back_ladb(self) -> bool:
        try:
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'input', 'keyevent', '4'],
                check=True, timeout=3
            )
            return True
        except Exception as e:
            logger.error(f"返回失败 (LADB): {e}")
            return False

    def home(self) -> bool:
        """执行回到桌面操作"""
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._home_accessibility()
        elif self.mode == self.MODE_LADB:
            return self._home_ladb()
        else:
            logger.error("无可用的 Home 方式")
            return False

    def _home_accessibility(self) -> bool:
        try:
            response = requests.post(f"{self.helper_url}/home", timeout=5)
            if response.status_code == 200:
                return response.json().get('success', False)
            return False
        except Exception as e:
            logger.error(f"Home 失败 (无障碍): {e}")
            return False

    def _home_ladb(self) -> bool:
        try:
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'input', 'keyevent', '3'],
                check=True, timeout=3
            )
            return True
        except Exception as e:
            logger.error(f"Home 失败 (LADB): {e}")
            return False

    def get_current_app(self) -> Optional[Tuple[str, str]]:
        """
        获取当前前台应用信息

        Returns:
            (app_name, package_name)，失败返回 None
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._get_current_app_accessibility()
        elif self.mode == self.MODE_LADB:
            return self._get_current_app_ladb()
        else:
            return None

    def _get_current_app_accessibility(self) -> Optional[Tuple[str, str]]:
        try:
            response = requests.get(f"{self.helper_url}/current_app", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return (data.get('app_name', ''), data.get('package_name', ''))
            return None
        except Exception as e:
            logger.error(f"获取当前应用失败 (无障碍): {e}")
            return None

    def _get_current_app_ladb(self) -> Optional[Tuple[str, str]]:
        try:
            result = subprocess.run(
                ['adb', '-s', self.adb_device, 'shell',
                 'dumpsys', 'activity', 'activities'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.splitlines():
                if 'mResumedActivity' in line or 'mFocusedActivity' in line:
                    # 格式: mResumedActivity: ActivityRecord{... com.pkg/.Activity ...}
                    parts = line.strip().split()
                    for part in parts:
                        if '/' in part and '.' in part:
                            pkg = part.split('/')[0]
                            return (pkg, pkg)
            return None
        except Exception as e:
            logger.error(f"获取当前应用失败 (LADB): {e}")
            return None

    def launch_app(self, app_name: str = '', package_name: str = '') -> bool:
        """
        启动应用

        Args:
            app_name: 应用名称 (如 "淘宝"、"微信")
            package_name: 包名 (如 "com.tencent.mm")，优先使用
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._launch_app_accessibility(app_name, package_name)
        elif self.mode == self.MODE_LADB:
            return self._launch_app_ladb(app_name, package_name)
        else:
            logger.error("无可用的启动应用方式")
            return False

    def _launch_app_accessibility(self, app_name: str, package_name: str) -> bool:
        try:
            payload = {}
            if package_name:
                payload['package_name'] = package_name
            elif app_name:
                payload['app_name'] = app_name
            else:
                return False

            response = requests.post(
                f"{self.helper_url}/launch_app",
                json=payload, timeout=10
            )
            if response.status_code == 200:
                success = response.json().get('success', False)
                logger.debug(f"启动应用 {app_name or package_name}: {success}")
                return success
            return False
        except Exception as e:
            logger.error(f"启动应用失败 (无障碍): {e}")
            return False

    def _launch_app_ladb(self, app_name: str, package_name: str) -> bool:
        APP_PACKAGES = {
            "淘宝": "com.taobao.taobao",
            "闲鱼": "com.taobao.idlefish",
            "咸鱼": "com.taobao.idlefish",
            "京东": "com.jingdong.app.mall",
            "微信": "com.tencent.mm",
            "支付宝": "com.eg.android.AlipayGphone",
            "抖音": "com.ss.android.ugc.aweme",
            "快手": "com.smile.gifmaker",
            "微博": "com.sina.weibo",
            "小红书": "com.xingin.xhs",
            "美团": "com.sankuai.meituan",
            "饿了么": "me.ele",
            "拼多多": "com.xunmeng.pinduoduo",
            "高德地图": "com.autonavi.minimap",
            "百度地图": "com.baidu.BaiduMap",
            "QQ": "com.tencent.mobileqq",
            "哔哩哔哩": "tv.danmaku.bili",
            "B站": "tv.danmaku.bili",
            "QQ音乐": "com.tencent.qqmusic",
            "网易云音乐": "com.netease.cloudmusic",
        }
        pkg = package_name or APP_PACKAGES.get(app_name, '')
        if not pkg:
            logger.error(f"LADB 模式下未知应用: {app_name}")
            return False
        try:
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell',
                 'monkey', '-p', pkg, '-c',
                 'android.intent.category.LAUNCHER', '1'],
                check=True, timeout=5
            )
            logger.debug(f"启动应用 {pkg}: True")
            return True
        except Exception as e:
            logger.error(f"启动应用失败 (LADB): {e}")
            return False

    def input_text(self, text: str) -> bool:
        """
        输入文字
        
        Args:
            text: 要输入的文字
        
        Returns:
            是否成功
        """
        if self.mode == self.MODE_ACCESSIBILITY:
            return self._input_accessibility(text)
        elif self.mode == self.MODE_LADB:
            return self._input_ladb(text)
        else:
            logger.error("无可用的输入方式")
            return False
    
    def _input_accessibility(self, text: str) -> bool:
        """通过无障碍服务输入"""
        try:
            response = requests.post(
                f"{self.helper_url}/input",
                json={'text': text},
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                success = data.get('success', False)
                logger.debug(f"输入文字: {success}")
                return success
            
            return False
            
        except Exception as e:
            logger.error(f"输入失败 (无障碍): {e}")
            return False
    
    def _input_ladb(self, text: str) -> bool:
        """通过 LADB 输入（优先用 ADB Keyboard 支持中文，fallback 到 input text）"""
        try:
            encoded = base64.b64encode(text.encode('utf-8')).decode('utf-8')
            result = subprocess.run(
                ['adb', '-s', self.adb_device, 'shell',
                 'am', 'broadcast',
                 '-a', 'ADB_INPUT_B64',
                 '--es', 'msg', encoded],
                capture_output=True, timeout=5
            )
            if result.returncode == 0 and 'result=0' not in result.stdout.decode(errors='ignore'):
                logger.debug("输入文字 (ADB Keyboard): True")
                return True

            # ADB Keyboard 不可用，fallback（仅支持 ASCII）
            logger.debug("ADB Keyboard 不可用，fallback 到 input text")
            escaped = text.replace(' ', '%s')
            subprocess.run(
                ['adb', '-s', self.adb_device, 'shell', 'input', 'text', escaped],
                check=True, timeout=5
            )
            logger.debug("输入文字 (input text): True")
            return True

        except Exception as e:
            logger.error(f"输入失败 (LADB): {e}")
            return False


# 测试代码
if __name__ == '__main__':
    print("测试 PhoneController...")
    
    try:
        controller = PhoneController()
        print(f"当前模式: {controller.get_mode()}")
        
        # 测试截图
        print("测试截图...")
        img = controller.screenshot()
        if img:
            print(f"截图成功: {img.size}")
        else:
            print("截图失败")
        
        # 测试点击
        print("测试点击...")
        success = controller.tap(500, 500)
        print(f"点击结果: {success}")
        
    except Exception as e:
        print(f"错误: {e}")
