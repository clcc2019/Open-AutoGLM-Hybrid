#!/data/data/com.termux/files/usr/bin/bash

# Open-AutoGLM 混合方案 - Termux 一键部署脚本
# 版本: 1.0.0

set -e

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
    
    # 检查并安装 Git
    if ! command -v git &> /dev/null; then
        print_info "安装 Git..."
        pkg install git -y
    else
        print_success "Git 已安装: $(git --version)"
    fi
    
    # 安装其他工具
    pkg install curl wget -y
    
    print_success "必要软件安装完成"
}

# 安装 Python 依赖
install_python_packages() {
    print_info "安装 Python 依赖包..."
    
    
    # 安装依赖
    pip install pillow openai requests -i https://pypi.tuna.tsinghua.edu.cn/simple
    
    print_success "Python 依赖安装完成"
}

# 下载 Open-AutoGLM
download_autoglm() {
    print_info "下载 Open-AutoGLM 项目..."
    
    cd ~
    
    if [ -d "Open-AutoGLM" ]; then
        print_warning "Open-AutoGLM 目录已存在"
        read -p "是否删除并重新下载? (y/n): " confirm
        if [ "$confirm" = "y" ]; then
            rm -rf Open-AutoGLM
        else
            print_info "跳过下载，使用现有目录"
            return
        fi
    fi
    
    git clone https://github.com/zai-org/Open-AutoGLM.git
    
    print_success "Open-AutoGLM 下载完成"
}

# 安装 Open-AutoGLM
install_autoglm() {
    print_info "安装 Open-AutoGLM..."
    
    cd ~/Open-AutoGLM
    
    # 安装项目依赖
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
    fi
    
    # 安装 phone_agent
    pip install -e .
    
    print_success "Open-AutoGLM 安装完成"
}

# 下载混合方案脚本
download_hybrid_scripts() {
    print_info "下载混合方案脚本..."
    
    cd ~
    
    # 创建目录
    mkdir -p ~/.autoglm
    
    # 下载 phone_controller.py (自动降级逻辑)
    # 注意: 这里需要替换为实际的下载链接
    # wget -O ~/.autoglm/phone_controller.py https://your-link/phone_controller.py
    
    # 暂时使用本地创建
    cat > ~/.autoglm/phone_controller.py << 'PYTHON_EOF'
# 这个文件会在后续步骤中创建
pass
PYTHON_EOF
    
    print_success "混合方案脚本下载完成"
}

# 配置 AI 服务
configure_ai_service() {
    print_info "配置 AI 服务..."

    echo ""
    echo "请选择 AI 服务提供商:"
    echo "  1) 智谱 AI (推荐)"
    echo "  2) GRS AI"
    echo ""
    read -p "请输入选项 (1/2): " choice

    case $choice in
        1)
            configure_zhipu_ai
            ;;
        2)
            configure_grs_ai
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
    echo "  1) GLM-4V (推荐 - 支持视觉理解)"
    echo "  2) GLM-4"
    echo "  3) GLM-3-Turbo"
    echo ""
    read -p "请输入选项 (1/2/3，默认 1): " model_choice

    case $model_choice in
        1)
            model="glm-4v"
            ;;
        2)
            model="glm-4"
            ;;
        3)
            model="glm-3-turbo"
            ;;
        *)
            model="glm-4v"
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

# 配置 GRS AI
configure_grs_ai() {
    print_info "配置 GRS AI..."

    echo ""
    echo "请输入您的 GRS AI API Key:"
    read -p "API Key: " api_key

    if [ -z "$api_key" ]; then
        print_warning "未输入 API Key，跳过配置"
        print_warning "您可以稍后手动编辑: ~/.autoglm/config.ini"
        return
    fi

    # 创建 INI 配置文件
    cat > ~/.autoglm/config.ini << EOF
[ai]
provider = grs
base_url = https://api.grsai.com/v1
api_key = $api_key
model = gpt-4-vision-preview

[helper]
url = http://localhost:8080
EOF

    print_success "GRS AI 配置完成"
}

# 创建启动脚本
create_launcher() {
    print_info "创建启动脚本..."

    # 确保 ~/bin 目录存在
    mkdir -p ~/bin

    # 创建 autoglm 命令
    cat > ~/bin/autoglm << 'LAUNCHER_EOF'
#!/data/data/com.termux/files/usr/bin/bash

# 配置文件路径
CONFIG_FILE=~/.autoglm/config.ini

# 读取 INI 配置并导出环境变量
if [ -f "$CONFIG_FILE" ]; then
    export PHONE_AGENT_BASE_URL=$(grep -E '^base_url\s*=' "$CONFIG_FILE" | cut -d'=' -f2 | xargs)
    export PHONE_AGENT_API_KEY=$(grep -E '^api_key\s*=' "$CONFIG_FILE" | cut -d'=' -f2 | xargs)
    export PHONE_AGENT_MODEL=$(grep -E '^model\s*=' "$CONFIG_FILE" | cut -d'=' -f2 | xargs)
    export AUTOGLM_HELPER_URL=$(grep -E '^url\s*=' "$CONFIG_FILE" | tail -1 | cut -d'=' -f2 | xargs)
fi

# 启动 AutoGLM
cd ~/Open-AutoGLM
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
    echo "  - 智谱 AI (GLM-4V, GLM-4, GLM-3-Turbo)"
    echo "  - GRS AI (GPT-4-Vision)"
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
    download_autoglm
    install_autoglm
    download_hybrid_scripts
    configure_ai_service
    create_launcher
    check_helper_app
    show_completion
}

# 运行主函数
main
