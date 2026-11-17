from benchmarks.live_browser_validation import compare_visible_listings


def test_listing_comparison_uses_only_observed_price_and_rating_fields():
    comparison = compare_visible_listings([
        {
            "title": "Intro to Python",
            "price": "INR 1,250.00",
            "rating": "4.5 out of 5 stars",
        },
        {
            "title": "Python Projects",
            "price": "INR 999.50",
            "rating": "4.8 out of 5 stars",
        },
        {"title": "No visible price or rating", "price": "", "rating": ""},
    ])

    assert comparison["visible_listing_count"] == 3
    assert comparison["lowest_visible_price"]["title"] == "Python Projects"
    assert comparison["highest_visible_rating"]["title"] == "Python Projects"
    assert "not inferred" in comparison["limitations"]


def test_listing_comparison_leaves_missing_metrics_absent():
    comparison = compare_visible_listings([{"title": "Unpriced listing"}])

    assert comparison["visible_listing_count"] == 1
    assert "lowest_visible_price" not in comparison
    assert "highest_visible_rating" not in comparison
