from app.services.textproc import canonical_url, hamming, simhash, strip_html, url_hash

ARTICLE = (
    "Giá vàng miếng SJC hôm nay tăng mạnh thêm 1,5 triệu đồng mỗi lượng, lên mức cao kỷ lục "
    "trong bối cảnh giá vàng thế giới liên tục lập đỉnh mới do lo ngại lạm phát và căng thẳng địa chính trị. "
    "Các chuyên gia khuyến nghị người dân thận trọng khi mua vào ở vùng giá cao."
)


def test_simhash_near_duplicate_is_close_and_different_text_is_far():
    near = ARTICLE.replace("thận trọng", "cẩn trọng")
    other = "Đội tuyển bóng đá Việt Nam thắng 3-0 trước Indonesia tại vòng loại World Cup tối qua trên sân Mỹ Đình."
    assert hamming(simhash(ARTICLE), simhash(near)) <= 12
    assert hamming(simhash(ARTICLE), simhash(ARTICLE)) == 0
    assert hamming(simhash(ARTICLE), simhash(other)) > 16


def test_simhash_fits_signed_bigint():
    for text in (ARTICLE, "a b c", "xyz " * 50):
        assert -(2**63) <= simhash(text) < 2**63


def test_canonical_url_drops_tracking_and_fragment():
    a = "https://VnExpress.net/gia-vang-123.html?utm_source=rss&utm_campaign=x#box"
    assert canonical_url(a) == "https://vnexpress.net/gia-vang-123.html"
    assert url_hash(a) == url_hash("https://vnexpress.net/gia-vang-123.html")


def test_strip_html():
    raw = '<a href="x"><img src="y"/></a></br>Giá vàng &amp; USD   tăng'
    assert strip_html(raw) == "Giá vàng & USD tăng"
