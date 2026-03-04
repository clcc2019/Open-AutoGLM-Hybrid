#!/data/data/com.termux/files/usr/bin/bash

# Open-AutoGLM 混合方案 - Termux 一键部署脚本
# 版本: 1.0.0

set -e

# 获取脚本所在目录（即本地代码目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
export ANDROID_API_LEVEL=33

# 打印函数
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo ""
    echo "============================================================"
    echo "  Open-AutoGLM 混合方案 - 一键部署"
    echo "  版本: 1.0.0"
    echo "============================================================"
    echo ""
}

# 检查网络连接
check_network() {
    print_info "检查网络连接..."
    if ping -c 1 8.8.8.8 &> /dev/null; then
        print_success "网络连接正常"
    else
        print_error "网络连接失败，请检查网络设置"
        exit 1
    fi
}

# 更新软件包
update_packages() {
    print_info "更新软件包列表..."
    pkg update -y
    print_success "软件包列表更新完成"
}

# 安装必要软件
install_dependencies() {
    print_info "安装必要软件..."
    
    # 检查并安装 Python
    if ! command -v python &> /dev/null; then
        print_info "安装 Python..."
        pkg install python -y
    else
        print_success "Python 已安装: $(python --version)"
    fi
    
    # 安装其他工具
    pkg install curl -y
    
    print_success "必要软件安装完成"
}

# 安装 Python 依赖
install_python_packages() {
    print_info "安装 Python 依赖包..."

    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        pip install -r "$SCRIPT_DIR/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
    else
        pip install pillow openai zhipuai requests -i https://pypi.tuna.tsinghua.edu.cn/simple
    fi

    print_success "Python 依赖安装完成"
}

# 安装混合方案脚本（从本地复制）
install_hybrid_scripts() {
    print_info "安装混合方案脚本..."

    mkdir -p ~/.autoglm

    # 从脚本所在目录复制 Python 源文件
    for f in main.py ai_client.py phone_controller.py config.py requirements.txt; do
        if [ -f "$SCRIPT_DIR/$f" ]; then
            cp "$SCRIPT_DIR/$f" ~/.autoglm/
        else
            print_warning "未找到 $f，跳过"
        fi
    done

    print_success "混合方案脚本安装完成 -> ~/.autoglm/"

    # 放置示例配置文件
    cat > ~/.autoglm/config.example.ini << 'EXAMPLE_EOF'
# ============================================================
#  Open-AutoGLM 配置文件示例
#  复制本文件为 config.ini 并填入你的 API Key 即可使用:
#    cp config.example.ini config.ini
# ============================================================

[ai]
# provider 可选值: zhipu / openai
#   zhipu  - 智谱 AI (GLM-4V-Plus, GLM-4V, GLM-4, GLM-3-Turbo)
#   openai - 任何兼容 OpenAI API 的服务
#            (OpenAI / DeepSeek / Moonshot / Ollama / vLLM 等)
provider = zhipu

# API 地址
#   智谱:     https://open.bigmodel.cn/api/paas/v4
#   OpenAI:   https://api.openai.com/v1
#   DeepSeek: https://api.deepseek.com/v1
#   Moonshot: https://api.moonshot.cn/v1
base_url = https://open.bigmodel.cn/api/paas/v4

# 你的 API Key（必填）
api_key = YOUR_API_KEY_HERE

# 模型名称
#   智谱推荐: glm-4v-plus (视觉理解最强), glm-4v, glm-4
#   OpenAI:   gpt-4o, gpt-4-turbo
#   DeepSeek: deepseek-chat
model = glm-4v-plus

# 可选: 思考模式 (true/false)
#   开启后模型会先进行推理再输出动作，适合推理模型:
#   OpenAI o1/o3, DeepSeek-R1 等
# thinking = false

# 可选: 最大 token 数
# max_tokens = 4096

# 可选: 温度 (0.0 ~ 1.0)
# temperature = 0.7

[helper]
# AutoGLM Helper APP 的 HTTP 地址
url = http://localhost:8080

# 控制模式: auto / accessibility / ladb
#   auto          - 自动检测（先尝试无障碍，不可用则降级到 LADB）
#   accessibility - 仅使用无障碍服务（需要 AutoGLM Helper APP）
#   ladb          - 仅使用 LADB/ADB
mode = auto
EXAMPLE_EOF

    print_info "示例配置已放置: ~/.autoglm/config.example.ini"
}

# 配置 AI 服务
configure_ai_service() {
    print_info "配置 AI 服务..."

    if [ -f ~/.autoglm/config.ini ]; then
        print_warning "检测到已有配置: ~/.autoglm/config.ini"
        read -p "是否重新配置? (y/n，默认 n): " redo
        if [ "$redo" != "y" ]; then
            print_info "保留现有配置"
            return
        fi
    fi

    echo ""
    echo "请选择 AI 服务提供商:"
    echo "  1) 智谱 AI (推荐，支持 GLM-4V 视觉模型)"
    echo "  2) OpenAI 兼容 (OpenAI / DeepSeek / Moonshot 等)"
    echo "  s) 跳过，稍后手动编辑 config.ini"
    echo ""
    read -p "请输入选项 (1/2/s): " choice

    case $choice in
        1)
            configure_zhipu_ai
            ;;
        2)
            configure_openai_compatible
            ;;
        s|S)
            print_info "跳过配置，从示例文件生成默认 config.ini ..."
            cp ~/.autoglm/config.example.ini ~/.autoglm/config.ini
            print_warning "请编辑 ~/.autoglm/config.ini 填入你的 API Key"
            ;;
        *)
            print_warning "无效选项，默认使用智谱 AI"
            configure_zhipu_ai
            ;;
    esac
}

# 配置智谱 AI
configure_zhipu_ai() {
    print_info "配置智谱 AI..."

    echo ""
    echo "请输入您的智谱 AI API Key:"
    read -p "API Key: " api_key

    if [ -z "$api_key" ]; then
        print_warning "未输入 API Key，跳过配置"
        print_warning "您可以稍后手动编辑: ~/.autoglm/config.ini"
        return
    fi

    echo ""
    echo "请选择智谱模型:"
    echo "  1) GLM-4V-Plus (推荐 - 最强视觉理解)"
    echo "  2) GLM-4V"
    echo "  3) GLM-4"
    echo "  4) GLM-3-Turbo"
    echo ""
    read -p "请输入选项 (1/2/3/4，默认 1): " model_choice

    case $model_choice in
        1)
            model="glm-4v-plus"
            ;;
        2)
            model="glm-4v"
            ;;
        3)
            model="glm-4"
            ;;
        4)
            model="glm-3-turbo"
            ;;
        *)
            model="glm-4v-plus"
            ;;
    esac

    print_info "已选择模型: $model"

    # 创建 INI 配置文件
    cat > ~/.autoglm/config.ini << EOF
[ai]
provider = zhipu
base_url = https://open.bigmodel.cn/api/paas/v4
api_key = $api_key
model = $model

[helper]
url = http://localhost:8080
EOF

    print_success "智谱 AI 配置完成"
}

# 配置 OpenAI 兼容 API
configure_openai_compatible() {
    print_info "配置 OpenAI 兼容 API..."

    echo ""
    echo "请输入 API Key:"
    read -p "API Key: " api_key

    if [ -z "$api_key" ]; then
        print_warning "未输入 API Key，跳过配置"
        print_warning "您可以稍后手动编辑: ~/.autoglm/config.ini"
        return
    fi

    echo ""
    echo "请输入 API Base URL (留空使用 OpenAI 官方):"
    echo "  常用地址:"
    echo "    OpenAI:   https://api.openai.com/v1"
    echo "    DeepSeek: https://api.deepseek.com/v1"
    echo "    Moonshot: https://api.moonshot.cn/v1"
    echo ""
    read -p "Base URL [https://api.openai.com/v1]: " base_url
    base_url=${base_url:-https://api.openai.com/v1}

    echo ""
    echo "请输入模型名称 (留空使用 gpt-4o):"
    read -p "模型 [gpt-4o]: " model
    model=${model:-gpt-4o}

    print_info "已选择: $base_url / $model"

    # 创建 INI 配置文件
    cat > ~/.autoglm/config.ini << EOF
[ai]
provider = openai
base_url = $base_url
api_key = $api_key
model = $model

[helper]
url = http://localhost:8080
EOF

    print_success "OpenAI 兼容 API 配置完成"
}

# 创建启动脚本
create_launcher() {
    print_info "创建启动脚本..."

    # 确保 ~/bin 目录存在
    mkdir -p ~/bin

    # 创建 autoglm 命令
    cat > ~/bin/autoglm << 'LAUNCHER_EOF'
#!/data/data/com.termux/files/usr/bin/bash

# 启动 AutoGLM（配置由 Python config.py 从 ~/.autoglm/config.ini 读取）
cd ~/.autoglm
python main.py "$@"
LAUNCHER_EOF

    chmod +x ~/bin/autoglm

    # 确保 ~/bin 在 PATH 中
    if ! grep -q 'export PATH=$PATH:~/bin' ~/.bashrc; then
        echo 'export PATH=$PATH:~/bin' >> ~/.bashrc
    fi

    print_success "启动脚本创建完成"
}

# 检查 AutoGLM Helper
check_helper_app() {
    print_info "检查 AutoGLM Helper APP..."
    
    echo ""
    echo "请确保您已经:"
    echo "1. 安装了 AutoGLM Helper APK"
    echo "2. 开启了无障碍服务权限"
    echo ""
    
    read -p "是否已完成以上步骤? (y/n): " confirm
    
    if [ "$confirm" != "y" ]; then
        print_warning "请先完成以上步骤，然后重新运行部署脚本"
        print_info "APK 文件位置: 项目根目录/AutoGLM-Helper.apk"
        print_info "安装命令: adb install AutoGLM-Helper.apk"
        exit 0
    fi
    
    # 测试连接
    print_info "测试 AutoGLM Helper 连接..."
    
    if curl -s http://localhost:8080/status > /dev/null 2>&1; then
        print_success "AutoGLM Helper 连接成功！"
    else
        print_warning "无法连接到 AutoGLM Helper"
        print_info "这可能是因为:"
        print_info "1. AutoGLM Helper 未运行"
        print_info "2. 无障碍服务未开启"
        print_info "3. HTTP 服务器未启动"
        print_info ""
        print_info "请检查后重试"
    fi
}

# 显示完成信息
show_completion() {
    print_success "部署完成！"

    echo ""
    echo "============================================================"
    echo "  部署成功！"
    echo "============================================================"
    echo ""
    echo "支持的 AI 服务:"
    echo "  - 智谱 AI (GLM-4V-Plus, GLM-4V, GLM-4, GLM-3-Turbo)"
    echo "  - OpenAI 兼容 (OpenAI / DeepSeek / Moonshot 等)"
    echo ""
    echo "使用方法:"
    echo "  1. 确保 AutoGLM Helper 已运行并开启无障碍权限"
    echo "  2. 在 Termux 中输入: autoglm"
    echo "  3. 输入任务，如: 打开淘宝搜索蓝牙耳机"
    echo ""
    echo "配置文件:"
    echo "  ~/.autoglm/config.ini"
    echo ""
    echo "启动命令:"
    echo "  autoglm"
    echo ""
    echo "切换 AI 服务:"
    echo "  编辑 ~/.autoglm/config.ini 修改以下配置:"
    echo "  - base_url (API 地址)"
    echo "  - api_key (API 密钥)"
    echo "  - model (模型名称)"
    echo ""
    echo "故障排除:"
    echo "  - 检查 AutoGLM Helper 是否运行"
    echo "  - 检查无障碍权限是否开启"
    echo "  - 测试连接: curl http://localhost:8080/status"
    echo ""
    echo "============================================================"
    echo ""
}

# 主函数
main() {
    print_header
    
    # 检查是否在 Termux 中运行
    if [ ! -d "/data/data/com.termux" ]; then
        print_error "此脚本必须在 Termux 中运行！"
        exit 1
    fi
    
    # 执行部署步骤
    check_network
    update_packages
    install_dependencies
    install_python_packages
    install_hybrid_scripts
    configure_ai_service
    create_launcher
    check_helper_app
    show_completion
}

# 运行主函数
main
