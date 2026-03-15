export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = "https://api.sofascore.com" + url.pathname + url.search;
    const res = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.sofascore.com/",
        "Accept": "application/json",
        "Origin": "https://www.sofascore.com"
      }
    });
    const data = await res.text();
    return new Response(data, {
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
      }
    });
  }
};
