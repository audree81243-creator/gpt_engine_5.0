def extract_raw_response(input_string: str) -> List[Dict[str, Any]]:
    """Parse ChatGPT's Server-Sent Events stream."""
    json_objects = []

    # Split by lines that start with "data: "
    lines = input_string.split("\n")

    for line in lines:
        # Skip empty lines and non-data lines
        if not line.strip() or not line.startswith("data: "):
            continue

        # Remove "data: " prefix
        json_str = line[6:].strip()

        # Skip special markers like [DONE]
        if json_str == "[DONE]":
            continue

        # Try to parse as JSON
        try:
            json_obj = json.loads(json_str)

            # Only include if it's a dictionary (object), not string or other types
            if isinstance(json_obj, dict):
                json_objects.append(json_obj)
        except json.JSONDecodeError:
            # Skip invalid JSON
            continue

    return json_objects

def reconstruct_content(events: List[Dict[str, Any]]) -> str:
    """Rebuild complete response from streaming chunks."""
    content_parts = []

    for event in events:
        # Extract content from delta messages
        if 'choices' in event and len(event['choices']) > 0:
            delta = event['choices'][0].get('delta', {})
            if 'content' in delta:
                content_parts.append(delta['content'])

    return ''.join(content_parts)

async def extract_sources(page: Page) -> List[Dict[str, Any]]:
    """Extract source citations from ChatGPT response."""

    try:
        # Click sources button if available
        sources_button = page.locator("button.group\\/footnote")
        if await sources_button.count() > 0:
            await sources_button.first.click()

            # Wait for modal
            modal = page.locator('[data-testid="screen-threadFlyOut"]')
            await modal.wait_for(state="visible", timeout=2000)

            # Extract links from modal
            links = modal.locator("a")
            link_count = await links.count()

            sources = []
            for i in range(link_count):
                link = links.nth(i)
                url = await link.get_attribute('href')
                text = await link.text_content()

                if url:
                    sources.append({
                        'url': url,
                        'title': text.strip() if text else '',
                        'position': i + 1
                    })

            return sources

    except Exception as e:
        print(f"Source extraction failed: {e}")
        return []

def extract_shopping_cards(events: List[Dict]) -> List[Dict[str, Any]]:
    """Extract product/shopping information from response."""

    shopping_cards = []

    for event in events:
        if 'shopping_card' in event:
            card_data = event['shopping_card']

            # Parse product information
            products = []
            for product in card_data.get('products', []):
                product_info = {
                    'title': product.get('title'),
                    'url': product.get('url'),
                    'price': product.get('price'),
                    'rating': product.get('rating'),
                    'num_reviews': product.get('num_reviews'),
                    'image_urls': product.get('image_urls', []),
                    'offers': []
                }

                # Parse merchant offers
                for offer in product.get('offers', []):
                    product_info['offers'].append({
                        'merchant_name': offer.get('merchant_name'),
                        'price': offer.get('price'),
                        'url': offer.get('url'),
                        'available': offer.get('available', True)
                    })

                products.append(product_info)

            shopping_cards.append({
                'tags': card_data.get('tags', []),
                'products': products
            })

    return shopping_cards

def extract_entities(events: List[Dict]) -> List[Dict[str, Any]]:
    """Extract named entities from ChatGPT response."""

    entities = []

    for event in events:
        if 'entities' in event:
            for entity in event['entities']:
                entities.append({
                    'type': entity.get('type'),
                    'name': entity.get('name'),
                    'confidence': entity.get('confidence'),
                    'context': entity.get('context')
                })

    return entities
