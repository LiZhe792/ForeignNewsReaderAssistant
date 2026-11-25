/* Exercise the visible progressive-disclosure control before opening a tool. */
async function clickReaderTool(page, selector) {
  const target=page.locator(selector).first();
  const nested=await target.evaluate(el=>Boolean(el.closest('.reader-more-tools:not([open])')));
  if(nested)await page.locator('.reader-more-tools > summary').click();
  await target.click();
}
module.exports={clickReaderTool};
