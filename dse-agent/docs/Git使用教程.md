# Git 使用教程（针对本项目 / 个人项目场景）

> 目标：能独立管理这个仓库、能讲清"你怎么用 Git 的"。
> 不需要学分支合并、rebase 那些协作场景，个人项目用不上，学了也容易忘。

---

## 一、一次性配置（只需做一次）

```bash
git config --global user.name "你的名字或 GitHub 用户名"
git config --global user.email "你的邮箱"
```

**邮箱建议用 GitHub 账号的邮箱**，这样提交能关联到你主页的贡献图。

检查是否配好：

```bash
git config --global --list
```

> 也可以只对当前仓库配置（把 `--global` 去掉），但个人项目建议全局配一次省事。

---

## 二、日常四步（记住这四步就够了）

```bash
git status              # 1. 看现在有什么改动
git diff                # 2. 看具体改了什么内容（可选）
git add <文件或 .>       # 3. 把要提交的改动放进暂存区
git commit -m "说明"     # 4. 提交
```

**最常用的一条**：

```bash
git add -A && git commit -m "fix: 修复多轮对话条件继承错误"
```

### 四步里最容易搞错的：add 和 commit 的区别

| 命令 | 作用 | 类比 |
|---|---|---|
| `git add` | 选中"这次要提交哪些改动" | 把东西放进快递箱 |
| `git commit` | 把箱子封上、贴标签 | 封箱、写上"里面是什么" |

`add` 之后又改了文件，**改动不会自动进箱子**，要再 `add` 一次。这是新手第一坑。

---

## 三、提交信息怎么写（面试会看）

用 `类型: 一句话说明` 的格式：

| 前缀 | 什么时候用 | 例子 |
|---|---|---|
| `feat` | 新增功能 | `feat(api): 新增会话列表接口` |
| `fix` | 修 bug | `fix(guard): 修复政策问题被误判为编造分数` |
| `docs` | 只改文档 | `docs: 补充日志排查指南` |
| `test` | 只改测试 | `test: 增加并发锁单测` |
| `refactor` | 重构但不改行为 | `refactor(query): 抽出 _build_where` |
| `chore` | 杂项（依赖、配置） | `chore: 补充 .gitignore` |

**判断标准**：一个月后你只看 message，能不能知道这次改了什么。写"update"、"修改"、"fix bug"等于没写。

---

## 四、个人项目要不要用分支？

**基本不用。** 个人项目直接在主分支（`main`）提交完全没问题，面试也不会问"你怎么管理分支"。

只需要知道两个概念（被问到能答）：

```bash
git branch                      # 看有哪些分支
git checkout -b feature/xxx     # 建一个新分支并切过去（做实验用）
git checkout main               # 切回主分支
git merge feature/xxx           # 把实验分支合进来
```

**什么时候真该用分支**：要做一个可能推翻的大改动（比如"整体换前端框架"），先在分支上试，不行就丢掉。

---

## 五、改错了怎么办（最实用的部分）

### 场景 1：改错了文件，还没 add

```bash
git checkout -- 文件路径        # 丢弃这个文件的改动（不可恢复！）
```

### 场景 2：已经 add 了，想撤回暂存（但保留改动）

```bash
git restore --staged 文件路径
```

### 场景 3：已经 commit 了，想改 message

```bash
git commit --amend -m "新的说明"
```

> ⚠️ 只能用**还没 push** 的提交；已经推上去的不要 amend。

### 场景 4：commit 之后发现漏了文件

```bash
git add 漏掉的文件
git commit --amend --no-edit     # 塞进上一次提交，不弹编辑器
```

### 场景 5：想看历史

```bash
git log --oneline                # 一行一条，最常用
git log --oneline -5             # 只看最近 5 条
git show <commit哈希>            # 看某次提交改了什么
```

---

## 六、`.gitignore` 是干什么的

告诉 Git"这些文件不要管"。本项目的 `.gitignore` 挡了四类：

| 类型 | 为什么挡 |
|---|---|
| `.env` | **里面是 API Key，泄漏会被盗刷** |
| `.venv/`、`node_modules/` | 依赖包，几百 MB，别人 `pip install` 就能装 |
| `__pycache__/`、`*.pyc` | 编译缓存，无意义 |
| `logs/`、`*.log`、`eval/report*.json` | 运行产物，不是源码 |

**新克隆的人怎么办？** 仓库里要有 `.env.example`（模板，不含真实 key），他复制成 `.env` 再填自己的。

```bash
cp .env.example .env    # 然后编辑 .env 填 key
```

> ⚠️ **本项目的 `.env` 已经进了 `.gitignore`**。任何时候 `git status` 里出现 `.env`，立刻停下来检查。

---

## 七、推到 GitHub

### 第一次推

```bash
# 1. 在 GitHub 网页上新建一个空仓库（不要勾选 README/gitignore）
# 2. 关联远程仓库
git remote add origin https://github.com/你的用户名/仓库名.git

# 3. 推上去
git branch -M main
git push -u origin main
```

> `-u` 的作用：记住"以后 `git push` 默认推到哪"，之后只需 `git push`。

### 之后每次推

```bash
git push
```

### 如果推不上去

| 报错 | 原因 | 解决 |
|---|---|---|
| `Authentication failed` | 密码认证已废弃 | 用 **Personal Access Token** 当密码，或配置 SSH key |
| `rejected (fetch first)` | 远程有你本地没有的提交 | `git pull --rebase` 后再 push |
| `remote origin already exists` | 已经配过远程 | `git remote set-url origin <新地址>` |

---

## 八、五个常见坑

| 坑 | 后果 | 怎么避免 |
|---|---|---|
| **把 `.env` 提交了** | API key 泄漏（GitHub 上有机器人 24 小时扫） | 提交前永远先看 `git status` |
| 提交了大文件（数据集/PDF） | 仓库变巨大，push 失败 | 先看 `.gitignore`，数据用 CSV 小样本 |
| `git add .` 一把梭 | 把日志、临时文件都提交了 | 用 `git add -A` 前先 `git status` 看一眼 |
| commit message 写"update" | 历史毫无信息量，面试减分 | 用 `类型: 说明` 格式 |
| 在错误分支上改东西 | 不知道该提交到哪 | 每次开工前 `git status` 看当前分支 |

---

## 九、面试可能问的 5 个 Git 问题

| 问题 | 答法（简短版） |
|---|---|
| **"你怎么用 Git？"** | "个人项目主分支开发，按功能拆分提交，message 用 `feat/fix/docs` 前缀；有实验性大改动时开分支，验证完再合并。" |
| **"commit 和 push 的区别？"** | "commit 是提交到本地仓库（可以随时改），push 是同步到远程（团队可见）。" |
| **"`.gitignore` 是干什么的？"** | "排除不该入库的文件——依赖包、编译产物、日志，**最关键的是 `.env` 这类凭据**，我在项目里就把它放在第一行。" |
| **"误提交了密码怎么办？"** | "先改密码/换 key（因为历史里已经有记录），再从仓库移除文件并加进 `.gitignore`；要清历史得用 `filter-repo`。" |
| **"多人协作怎么处理冲突？"** | "拉取时用 `pull --rebase` 保持线性；冲突手动解，解完 `add` 再 `rebase --continue`。"（个人项目没做过就说"了解流程，但项目里还没遇到"） |

---

## 十、本项目现在会怎么提交（归档说明）

因为前期开发时没有及时提交，现在按**模块一次性归档**成 8 次提交，让历史可读：

```
1. chore: 初始化项目结构与依赖配置
2. feat(data): 数据解析与入库管线
3. feat(agent): ReAct 循环与确定性查询工具
4. feat(rag): 政策语料向量检索
5. feat(guard): 防编造护栏、多轮记忆与参数归一
6. feat(web): SSE 流式接口与对话页
7. test: 评测集与单元测试
8. docs: 架构说明与踩坑复盘
```

**这不是伪造历史**——文件确实是一个模块一个模块写的，只是没及时提交。之后的改动（Vue 前端、Docker）会正常按次提交。

---

## 附：最常用命令速查

```bash
git status                    # 现在什么状态
git diff                      # 改了什么内容
git add -A                    # 全部加入暂存
git commit -m "feat: xxx"     # 提交
git log --oneline -10         # 最近 10 条历史
git push                      # 推到 GitHub
git pull --rebase             # 拉取（保持线性）
git checkout -- 文件           # 丢弃改动（危险）
git restore --staged 文件      # 从暂存撤回
```
