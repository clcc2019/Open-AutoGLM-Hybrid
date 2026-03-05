# GitHub Actions 签名 APK 配置指南

本指南将帮助您配置 GitHub Actions 自动构建带签名的 APK。

---

## 📋 目录

1. [生成签名密钥](#生成签名密钥)
2. [配置 GitHub Secrets](#配置-github-secrets)
3. [验证配置](#验证配置)
4. [构建带签名的 APK](#构建带签名的-apk)
5. [故障排除](#故障排除)

---

## 🔑 生成签名密钥

### 步骤 1: 生成 Keystore 文件

在本地计算机上运行以下命令生成签名密钥：

```bash
keytool -genkey -v -keystore autoglm-release.jks \
  -alias autoglm \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

**参数说明：**
- `-keystore autoglm-release.jks`: 密钥库文件名
- `-alias autoglm`: 密钥别名（记住这个值）
- `-keyalg RSA`: 密钥算法
- `-keysize 2048`: 密钥长度
- `-validity 10000`: 有效期（天）

**交互式输入：**
- 密钥库密码（记住这个密码）
- 密钥密码（可以与密钥库密码相同）
- 姓名、组织等信息（可选）

### 步骤 2: 验证 Keystore 文件

```bash
keytool -list -v -keystore autoglm-release.jks
```

输入密码后，应该能看到密钥信息。

### 步骤 3: 将 Keystore 转换为 Base64

**Linux/Mac:**
```bash
base64 -i autoglm-release.jks | pbcopy  # Mac
base64 autoglm-release.jks | xclip -selection clipboard  # Linux
```

**Windows (PowerShell):**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("autoglm-release.jks")) | Set-Clipboard
```

**手动复制：**
```bash
base64 autoglm-release.jks
```
复制输出的所有内容（很长的一串字符）。

---

## 🔐 配置 GitHub Secrets

### 步骤 1: 打开 Secrets 设置页面

1. 访问您的 GitHub 仓库
2. 点击 **Settings** (设置)
3. 在左侧菜单找到 **Secrets and variables** → **Actions**
4. 点击 **New repository secret**

### 步骤 2: 添加以下 Secrets

需要添加 **4 个 Secrets**：

#### 1. KEYSTORE_BASE64

- **Name**: `KEYSTORE_BASE64`
- **Value**: 步骤 1.3 中生成的 Base64 字符串（完整复制，包括换行符）

#### 2. KEYSTORE_PASSWORD

- **Name**: `KEYSTORE_PASSWORD`
- **Value**: 生成 keystore 时输入的密钥库密码

#### 3. KEY_ALIAS

- **Name**: `KEY_ALIAS`
- **Value**: 生成 keystore 时使用的别名（通常是 `autoglm`）

#### 4. KEY_PASSWORD

- **Name**: `KEY_PASSWORD`
- **Value**: 生成 keystore 时输入的密钥密码（如果与密钥库密码相同，可以设置相同的值）

### 步骤 3: 验证 Secrets

确保所有 4 个 Secrets 都已添加：

- ✅ `KEYSTORE_BASE64`
- ✅ `KEYSTORE_PASSWORD`
- ✅ `KEY_ALIAS`
- ✅ `KEY_PASSWORD`

---

## ✅ 验证配置

### 方法 1: 创建标签触发构建

```bash
# 创建标签
git tag v1.0.0

# 推送标签
git push origin v1.0.0
```

### 方法 2: 手动触发工作流

1. 访问 GitHub 仓库的 **Actions** 页面
2. 选择 **Build Android APK** 工作流
3. 点击 **Run workflow**
4. 选择分支（通常是 `main`）
5. 点击 **Run workflow**

### 检查构建结果

1. 在 Actions 页面查看构建日志
2. 构建成功后，在 **Artifacts** 部分应该能看到：
   - `AutoGLM-Helper-Release-Signed` (带签名的 APK)

---

## 🚀 构建带签名的 APK

### 自动构建（推荐）

**方式 1: 推送标签**
```bash
git tag v1.0.0
git push origin v1.0.0
```

**方式 2: 手动触发**
- 在 GitHub Actions 页面手动运行工作流

### 构建流程

1. ✅ 检出代码
2. ✅ 设置 JDK 17
3. ✅ 构建 Debug APK（用于测试）
4. ✅ 从 Secrets 恢复 Keystore 文件
5. ✅ 使用签名配置构建 Release APK
6. ✅ 上传签名的 APK 到 Artifacts
7. ✅ 清理敏感文件

### 下载签名的 APK

1. 访问 Actions 页面
2. 找到成功的构建
3. 滚动到底部 **Artifacts** 部分
4. 下载 `AutoGLM-Helper-Release-Signed`
5. 解压 ZIP 文件，得到 `app-release.apk`

---

## 🔍 验证 APK 签名

下载 APK 后，可以验证签名：

```bash
# 检查 APK 签名信息
apksigner verify --print-certs app-release.apk

# 或使用 jarsigner (Java 工具)
jarsigner -verify -verbose -certs app-release.apk
```

如果看到签名信息，说明 APK 已正确签名。

---

## 🐛 故障排除

### 问题 1: 构建失败 - Keystore 文件不存在

**错误信息：**
```
FileNotFoundException: keystore.jks (No such file or directory)
```

**原因：**
- `KEYSTORE_BASE64` Secret 未配置或为空
- Base64 编码错误

**解决：**
1. 检查 GitHub Secrets 中是否配置了 `KEYSTORE_BASE64`
2. 重新生成 Base64 编码（确保完整复制）
3. 验证 Base64 字符串是否正确：
   ```bash
   echo "YOUR_BASE64_STRING" | base64 -d > test.jks
   keytool -list -keystore test.jks
   ```

### 问题 2: 构建失败 - 密码错误

**错误信息：**
```
java.security.UnrecoverableKeyException: Cannot recover key
```

**原因：**
- `KEYSTORE_PASSWORD` 或 `KEY_PASSWORD` 错误
- 密钥别名 `KEY_ALIAS` 错误

**解决：**
1. 验证密码是否正确：
   ```bash
   keytool -list -v -keystore autoglm-release.jks
   ```
2. 检查 GitHub Secrets 中的密码是否与生成时一致
3. 确认 `KEY_ALIAS` 与生成时使用的别名一致

### 问题 3: Base64 编码问题

**错误信息：**
```
Invalid keystore format
```

**原因：**
- Base64 编码不完整
- 复制时遗漏了部分内容

**解决：**
1. 重新生成 Base64：
   ```bash
   base64 autoglm-release.jks > keystore_base64.txt
   ```
2. 检查文件大小是否合理（通常几 KB）
3. 完整复制 Base64 字符串到 GitHub Secrets

### 问题 4: 构建成功但 APK 未签名

**检查：**
```bash
apksigner verify --print-certs app-release.apk
```

**原因：**
- Secrets 未正确配置
- 工作流条件判断失败

**解决：**
1. 确认所有 4 个 Secrets 都已配置
2. 检查工作流日志，确认是否执行了签名步骤
3. 确保使用标签触发构建（`git tag`）

### 问题 5: 权限问题

**错误信息：**
```
Permission denied: keystore.jks
```

**解决：**
工作流中已包含 `chmod 600` 命令设置权限，如果仍有问题，检查工作流文件。

---

## 📝 最佳实践

### 1. 密钥安全

- ✅ **永远不要**将 keystore 文件提交到 Git
- ✅ **永远不要**在代码中硬编码密码
- ✅ 使用 GitHub Secrets 存储敏感信息
- ✅ 定期备份 keystore 文件（安全存储）

### 2. 密钥管理

- ✅ 为不同环境使用不同的密钥
- ✅ 记录密钥的用途和有效期
- ✅ 密钥丢失后无法恢复，请妥善保管

### 3. 构建优化

- ✅ 只在发布版本时构建签名 APK（使用标签触发）
- ✅ Debug 版本不需要签名
- ✅ 使用缓存加速构建（工作流已配置）

---

## 🔄 更新签名配置

如果需要更新签名配置：

1. **生成新的 Keystore**（如果需要）
2. **更新 GitHub Secrets**：
   - 更新 `KEYSTORE_BASE64`
   - 更新 `KEYSTORE_PASSWORD`（如果更改）
   - 更新 `KEY_ALIAS`（如果更改）
   - 更新 `KEY_PASSWORD`（如果更改）
3. **触发新的构建**验证配置

---

## 📚 相关文档

- [Android 应用签名文档](https://developer.android.com/studio/publish/app-signing)
- [GitHub Actions Secrets 文档](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [keytool 命令参考](https://docs.oracle.com/javase/8/docs/technotes/tools/unix/keytool.html)

---

## ✅ 检查清单

配置前确认：

- [ ] 已生成 keystore 文件
- [ ] 已记录所有密码和别名
- [ ] 已将 keystore 转换为 Base64
- [ ] 已在 GitHub 添加 4 个 Secrets
- [ ] 已创建标签或手动触发构建
- [ ] 已验证构建成功
- [ ] 已下载并验证 APK 签名

---

## 🎉 完成！

配置完成后，每次推送标签时，GitHub Actions 会自动构建带签名的 Release APK。

**无需本地安装 Android Studio 或配置签名环境！**

---

*最后更新: 2024-12-10*
