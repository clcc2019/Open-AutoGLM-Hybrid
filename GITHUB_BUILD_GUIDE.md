# GitHub Actions 自动构建 APK 指南

## 📋 概述

本项目已配置 GitHub Actions 自动构建，无需本地安装 Android Studio 或 Gradle，只需推送代码到 GitHub 即可自动构建 APK。

---

## 🚀 快速开始（3 步）

### 步骤 1: 创建 GitHub 仓库

1. **登录 GitHub**
   - 访问: https://github.com
   - 登录您的账号

2. **创建新仓库**
   - 点击右上角 "+" → "New repository"
   - 仓库名: `Open-AutoGLM-Hybrid`
   - 可见性: Public 或 Private (都可以)
   - 不要勾选 "Initialize this repository with a README"
   - 点击 "Create repository"

### 步骤 2: 上传项目代码

**方式 A: 使用 Git 命令行** (推荐)

```bash
# 1. 进入项目目录
cd Open-AutoGLM-Hybrid

# 2. 初始化 Git 仓库
git init

# 3. 添加所有文件
git add .

# 4. 提交
git commit -m "Initial commit"

# 5. 添加远程仓库 (替换为您的仓库地址)
git remote add origin https://github.com/YOUR_USERNAME/Open-AutoGLM-Hybrid.git

# 6. 推送代码
git branch -M main
git push -u origin main
```

**方式 B: 使用 GitHub Desktop**

1. 下载并安装 GitHub Desktop
2. File → Add Local Repository
3. 选择 `Open-AutoGLM-Hybrid` 目录
4. Publish repository

**方式 C: 使用 GitHub 网页上传**

1. 在 GitHub 仓库页面点击 "uploading an existing file"
2. 将项目文件夹拖拽到页面
3. 点击 "Commit changes"

### 步骤 3: 等待自动构建

1. **查看构建状态**
   - 在 GitHub 仓库页面，点击 "Actions" 标签
   - 应该看到一个正在运行的工作流 "Build Android APK"
   - 等待约 5-10 分钟

2. **下载 APK**
   - 构建完成后，点击工作流名称
   - 在 "Artifacts" 部分找到 "AutoGLM-Helper-Debug"
   - 点击下载 (ZIP 文件)
   - 解压后得到 `app-debug.apk`

---

## 📦 下载构建好的 APK

### 方式 1: 从 Actions 下载

1. 访问仓库的 Actions 页面
   ```
   https://github.com/YOUR_USERNAME/Open-AutoGLM-Hybrid/actions
   ```

2. 点击最新的成功构建 (绿色勾号)

3. 滚动到底部 "Artifacts" 部分

4. 点击 "AutoGLM-Helper-Debug" 下载

5. 解压 ZIP 文件，得到 APK

### 方式 2: 使用 GitHub CLI

```bash
# 安装 GitHub CLI
# https://cli.github.com/

# 下载最新的 APK
gh run download --name AutoGLM-Helper-Debug
```

---

## 🔄 触发构建的方式

### 1. 推送代码 (自动触发)

```bash
git add .
git commit -m "Update code"
git push
```

推送后自动开始构建。

### 2. 手动触发

1. 访问 Actions 页面
2. 点击 "Build Android APK" 工作流
3. 点击 "Run workflow" 按钮
4. 选择分支 (通常是 main)
5. 点击 "Run workflow"

### 3. Pull Request (自动触发)

创建 Pull Request 时也会自动构建，用于测试。

---

## 🏷️ 发布正式版本

### 创建 Release

1. **打标签**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. **自动构建 Release APK**
   - GitHub Actions 会自动构建 Release 版本
   - 如果配置了签名（见下方），APK 会自动签名
   - APK 会经过优化和混淆

3. **创建 GitHub Release**
   - 在 GitHub 仓库页面，点击 "Releases"
   - 点击 "Create a new release"
   - 选择刚才创建的标签 (v1.0.0)
   - 填写 Release 说明
   - 上传构建好的 APK
   - 点击 "Publish release"

---

## 🔐 配置 APK 签名（可选）

### 为什么需要签名？

- ✅ 应用商店要求（Google Play、华为应用市场等）
- ✅ 应用更新需要相同签名
- ✅ 提高应用安全性

### 快速配置

**详细步骤请参考**: [`docs/GITHUB_SIGNING_GUIDE.md`](../docs/GITHUB_SIGNING_GUIDE.md)

**简要步骤：**

1. **生成签名密钥**
   ```bash
   keytool -genkey -v -keystore autoglm-release.jks \
     -alias autoglm -keyalg RSA -keysize 2048 -validity 10000
   ```

2. **转换为 Base64**
   ```bash
   base64 autoglm-release.jks
   ```

3. **在 GitHub 添加 Secrets**
   - `KEYSTORE_BASE64`: Base64 编码的 keystore 文件
   - `KEYSTORE_PASSWORD`: 密钥库密码
   - `KEY_ALIAS`: 密钥别名（通常是 `autoglm`）
   - `KEY_PASSWORD`: 密钥密码

4. **触发构建**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

构建完成后，下载的 APK 将是已签名的版本。

**注意：** 如果不配置签名，Release APK 将未签名（仅用于测试）。

---

## 🔧 自定义构建

### 修改构建配置

编辑 `.github/workflows/build-apk.yml`:

```yaml
# 修改 Java 版本
- name: Set up JDK 17
  uses: actions/setup-java@v4
  with:
    java-version: '17'  # 改为 11 或 17

# 添加签名配置
- name: Sign APK
  run: |
    # 添加签名步骤
```

### 添加自动发布

在 `.github/workflows/build-apk.yml` 末尾添加:

```yaml
- name: Create Release
  if: startsWith(github.ref, 'refs/tags/')
  uses: softprops/action-gh-release@v1
  with:
    files: |
      android-app/app/build/outputs/apk/release/app-release.apk
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## 📊 构建状态徽章

在 README.md 中添加构建状态徽章:

```markdown
![Build Status](https://github.com/YOUR_USERNAME/Open-AutoGLM-Hybrid/workflows/Build%20Android%20APK/badge.svg)
```

效果: ![Build Status](https://img.shields.io/badge/build-passing-brightgreen)

---

## 🐛 故障排除

### 问题 1: 构建失败 - Gradle 版本不兼容

**错误信息**:
```
Gradle version X.X is required. Current version is Y.Y
```

**解决**:
编辑 `gradle/wrapper/gradle-wrapper.properties`:
```properties
distributionUrl=https\://services.gradle.org/distributions/gradle-8.0-bin.zip
```

### 问题 2: 构建失败 - SDK 版本问题

**错误信息**:
```
SDK location not found
```

**解决**:
GitHub Actions 会自动处理，无需手动配置。如果仍有问题，检查 `build.gradle.kts` 中的 `compileSdk` 版本。

### 问题 3: 无法下载 Artifacts

**原因**:
- Artifacts 保留时间有限 (默认 90 天)
- 需要登录 GitHub

**解决**:
- 重新触发构建
- 或使用 GitHub CLI 下载

### 问题 4: 构建时间过长

**原因**:
- 首次构建需要下载依赖
- Gradle 缓存未命中

**优化**:
在 `.github/workflows/build-apk.yml` 中添加缓存:

```yaml
- name: Cache Gradle packages
  uses: actions/cache@v3
  with:
    path: |
      ~/.gradle/caches
      ~/.gradle/wrapper
    key: ${{ runner.os }}-gradle-${{ hashFiles('**/*.gradle*', '**/gradle-wrapper.properties') }}
    restore-keys: |
      ${{ runner.os }}-gradle-
```

---

## 💡 最佳实践

### 1. 使用分支保护

在 Settings → Branches 中:
- 启用 "Require status checks to pass"
- 选择 "Build Android APK"
- 确保代码合并前构建成功

### 2. 定期清理 Artifacts

Artifacts 会占用存储空间:
- Settings → Actions → General
- 设置 Artifact retention: 30 days

### 3. 使用 Secrets 存储敏感信息

如果需要签名:
- Settings → Secrets and variables → Actions
- 添加以下 Secrets:
  - `KEYSTORE_BASE64`: Base64 编码的 keystore 文件
  - `KEYSTORE_PASSWORD`: 密钥库密码
  - `KEY_ALIAS`: 密钥别名
  - `KEY_PASSWORD`: 密钥密码
- 详细配置请参考 [`docs/GITHUB_SIGNING_GUIDE.md`](../docs/GITHUB_SIGNING_GUIDE.md)

---

## 📈 构建时间估算

| 阶段 | 时间 |
|------|------|
| Checkout 代码 | 10-20 秒 |
| 设置 JDK | 20-30 秒 |
| 下载 Gradle | 30-60 秒 |
| 下载依赖 | 1-2 分钟 |
| 编译代码 | 2-3 分钟 |
| 打包 APK | 30-60 秒 |
| 上传 Artifact | 10-20 秒 |
| **总计** | **5-10 分钟** |

*首次构建较慢，后续构建会快很多（有缓存）*

---

## 🎓 进阶功能

### 多变体构建

构建不同版本的 APK:

```yaml
- name: Build All Variants
  run: |
    cd android-app
    ./gradlew assembleDebug assembleBeta assembleRelease
```

### 自动化测试

添加测试步骤:

```yaml
- name: Run Unit Tests
  run: |
    cd android-app
    ./gradlew test

- name: Upload Test Results
  uses: actions/upload-artifact@v4
  with:
    name: test-results
    path: android-app/app/build/reports/tests/
```

### 通知

构建完成后发送通知:

```yaml
- name: Notify on Success
  if: success()
  run: |
    curl -X POST https://your-webhook-url \
      -d "Build succeeded!"
```

---

## 📞 需要帮助？

如果遇到问题:
1. 查看 Actions 日志详细错误信息
2. 搜索错误信息
3. 提交 Issue 到 GitHub
4. 查看 GitHub Actions 文档: https://docs.github.com/actions

---

## ✅ 检查清单

部署前确认:

- [ ] 已创建 GitHub 仓库
- [ ] 已上传所有项目文件
- [ ] `.github/workflows/build-apk.yml` 文件存在
- [ ] `gradlew` 文件有执行权限
- [ ] `gradle-wrapper.jar` 文件存在
- [ ] 推送代码后查看 Actions 页面

---

## 🎉 完成！

现在您可以:
1. ✅ 推送代码自动构建
2. ✅ 手动触发构建
3. ✅ 下载构建好的 APK
4. ✅ 创建 Release 版本

**无需本地安装任何工具！**

---

*最后更新: 2024-12-10*
