# 隧道收敛测缝台

测量员登记里程桩号与收敛毫米值。接口进程内后台线程认领待判行（不另起 worker 容器），按绝对值是否不超过 3.0 mm 给出合格或超限。页面是 Svelte。

页眉挂**峰值墙**：左列断面、右列拱顶历史峰值与峰值时刻，带刷新和锁定。峰值只从办结集合重算，未办结的断面不虚填；锁定把那一刻的峰值与时刻压进**锁区**（只读副本），之后新办结只刷还没锁的列，已锁的列不再变。锁区专页任何角色都只能观看。

## 技术栈

- 后端：Flask、Gunicorn、SQLAlchemy、进程内认领线程
- 前端：Svelte、Vite、nginx 反代 `/api`
- 数据库：PostgreSQL 16

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3201 |
| 接口 | http://localhost:8201 |
| PostgreSQL | localhost:54401（库名 `tunnelconv`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 可提交、可点锁 |
| inspector | insp123456 | 只读（只许观看） |

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/logs | 办结集合（含待处理行） |
| POST | /api/logs | 提交读数（仅测量员） |
| GET | /api/peaks | 峰值墙：各断面峰值与时刻；已锁的列回锁区快照 |
| POST | /api/peaks/lock | 锁定某断面（仅测量员）。只收桩号，峰值与时刻由服务端按办结集合重算，客户端手改的数字一律不采信；未办结 400，已锁 409 |
| GET | /api/locks | 锁区专页数据：锁定时刻压成的只读副本 |

## 启动

```bash
cd projects/21-tunnel-convergence-desk
docker compose up --build
```

健康检查：`GET http://localhost:8201/api/health`

## 种子

| 桩号 | 收敛 | 结论 |
|------|------|------|
| K12+180 | 1.2 mm | 合格 |
| K18+040 | 5.6 mm | 超限 |
