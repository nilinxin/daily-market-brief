import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.a_stock_main_money_briefing import Candidate, _freshness, is_risky_name, score_candidate


CONFIG = {
    "healthy_volume_ratio": [0.75, 2.2],
    "minimum_amount": {"1030": 80_000_000, "1330": 150_000_000},
}


def candidate(**changes):
    values = dict(
        code="600000",
        name="样本股份",
        sector="半导体",
        price=10.2,
        change=1.0,
        amount=500_000_000,
        turnover=2.5,
        volume_ratio=1.3,
        market_cap=20_000_000_000,
        current_main_flow=30_000_000,
        current_flow_ratio=6.0,
        sector_change=2.2,
        sector_flow=1_000_000_000,
        ret_3d=2.0,
        ret_5d=3.0,
        ret_10d=5.0,
        ret_20d=8.0,
        flow_3d=50_000_000,
        flow_5d=80_000_000,
        flow_10d=100_000_000,
        positive_flow_days_5=4,
        ma5=10.1,
        ma10=10.0,
        ma20=9.9,
        ma60=9.5,
        range_position_60d=48.0,
        support=9.9,
        resistance=11.0,
        upper_shadow=0.8,
    )
    values.update(changes)
    return Candidate(**values)


class ScoreTests(unittest.TestCase):
    def test_low_position_with_inflow_scores_above_threshold(self):
        result = score_candidate(candidate(), CONFIG)
        self.assertGreaterEqual(result.total_score, 60)
        self.assertLessEqual(result.total_score, 100)

    def test_high_position_outflow_is_penalized(self):
        result = score_candidate(
            candidate(range_position_60d=94, ret_10d=32, flow_5d=-90_000_000, volume_ratio=4.2, upper_shadow=4.0),
            CONFIG,
        )
        self.assertGreaterEqual(result.risk_deduction, 8)
        self.assertLess(result.total_score, score_candidate(candidate(), CONFIG).total_score)

    def test_st_and_delisting_names_are_rejected(self):
        self.assertTrue(is_risky_name("*ST样本"))
        self.assertTrue(is_risky_name("退市样本"))
        self.assertFalse(is_risky_name("正常股份"))

    def test_stale_quote_date_is_treated_as_closed_market(self):
        now = datetime(2026, 7, 2, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        old = datetime(2026, 7, 1, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        fresh, _ = _freshness([{"f124": int(old.timestamp())}], now)
        self.assertFalse(fresh)


if __name__ == "__main__":
    unittest.main()
