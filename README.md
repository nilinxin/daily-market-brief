# 自动化每日市场简报

这个项目每天自动生成 Markdown 格式的市场简报，包含美股每日简报和 A 股开盘前简报。

## 本地运行

```bash
pip install -r requirements.txt
python -m src.main --mode full
```

生成文件会保存到 `reports/`。

可选模式：

- `full`：生成完整简报。
- `us`：生成美股简报文件。
- `cn`：生成 A 股盘前简报文件。

## GitHub Actions 自动运行

`.github/workflows/daily-market-brief.yml` 已配置定时运行：

- 北京时间周一到周五 07:30 左右。
- 北京时间周一到周五 08:50 左右。

GitHub Actions 使用 UTC 时间，配置中已经换算。

## 修改关注股票和板块

编辑 `config/watchlists.json`：

- `hot_us_stocks`：热门美股。
- `focus_topics`：关注板块和关键词。
- `global_watch`：期货、汇率、商品等外围指标。

## 数据源

第一版默认不需要 API key，主要使用公开行情和新闻来源。公开来源可能会延迟、变更或临时不可用，程序会把抓取失败的信息写到报告底部和 `logs/market-brief.log`。

后续可以加入 Finnhub、Alpha Vantage、邮件、飞书、Telegram 等密钥。不要把真实密钥写进代码，应放在 GitHub Secrets。

## 免责声明

报告仅供观察，不构成投资建议。
