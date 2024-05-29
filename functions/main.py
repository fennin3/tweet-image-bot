from firebase_functions import https_fn
from firebase_admin import initialize_app
import openai
from PIL import Image, ImageDraw, ImageFont
import tweepy
import requests
import textwrap
import tweepy.models
from dotenv import load_dotenv
import os
from google.cloud import storage


load_dotenv()
initialize_app()

# Initialize the Google Cloud Storage client
storage_client = storage.Client()
bucket_name = os.environ.get("BUCKET_NAME")
bucket = storage_client.bucket(bucket_name)


# Twitter API credentials
consumer_key = os.environ.get("CONSUMER_KEY")
consumer_secret = os.environ.get("CONSUMER_SECRET")
access_token = os.environ.get("ACCESS_TOKEN")
access_token_secret = os.environ.get("ACCESS_TOKEN_SECRET")
bearer_token = os.environ.get("BEARER_TOKEN")

output_filename = "quote_image.png"

# Twitter Config
auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
auth.set_access_token(
    access_token,
    access_token_secret,
)
newapi = tweepy.Client(
    bearer_token=bearer_token,
    access_token=access_token,
    access_token_secret=access_token_secret,
    consumer_key=consumer_key,
    consumer_secret=consumer_secret,
)
api = tweepy.API(auth)


openai.api_key = os.environ.get("OPENAI_SK")


BASE_DIR = os.path.dirname(__file__)


def download_file_from_gcs(filename):
    blob = bucket.blob(filename)
    local_path = f"/tmp/{filename}"
    blob.download_to_filename(local_path)
    return local_path


# Function to get a quote from FavQs API
def get_quote():
    response = requests.get("https://favqs.com/api/qotd")
    if response.status_code == 200:
        quote_data = response.json()
        quote = quote_data["quote"]["body"]
        author = quote_data["quote"]["author"]
        return f'"{quote}" - {author}'
    else:
        return None


def rate_and_improve_quote(quote):
    prompt = (
        f"Rate the following quote from 1 to 10, where 10 is the best, return an improved version of the text without any additional text and add a 2 to 5 word caption for twitter post based on the quote:\n\n"
        f"Quote: {quote}\n\n"
        "Rating: \n"
        "Improved Quote: \n"
        "Caption: "
    )
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "You are an assistant that rates and improves quotes.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=100,
        temperature=0.7,
    )
    result = response.choices[0].message["content"].strip().split("\n")
    result = [text for text in result if text]
    rating = result[0].replace("Rating:", "").strip()
    improvement = (
        result[1].replace("Improved Quote:", "").strip() if len(result) > 1 else quote
    )
    caption = result[2].replace("Caption:", "").strip()
    return rating, improvement, caption


def prep_text(quote: str):
    quote, author = quote.split(" - ")
    quote = quote.replace('"', "")
    author = f"@ {author}"
    return (quote, author)


def create_image_with_quote(quote: str):
    quote, author = prep_text(quote)
    bg_img_path = download_file_from_gcs("bg.jpg")
    background = Image.open(bg_img_path).resize((2000, 2000))
    draw = ImageDraw.Draw(background)
    image_width, image_height = background.size

    margin = 50
    font_size = 80
    roboto_font_path = download_file_from_gcs("roboto.ttf")
    font = ImageFont.truetype(roboto_font_path, font_size)

    lines = textwrap.wrap(quote, width=40)
    total_text_height = (
        sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines)
        + (len(lines) - 1) * 10
    )

    # Adjust font size if text is too tall for the image
    while total_text_height > image_height - 2 * margin and font_size > 10:
        font_size -= 2
        total_text_height = (
            sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines)
            + (len(lines) - 1) * 10
        )

    y = ((image_height - total_text_height) / 2) - 50

    for line in lines:
        text_width, text_height = draw.textbbox((0, 0), line, font=font)[2:]
        x = (image_width - text_width) / 2
        draw.text((x, y), line, font=font, fill="black")
        y += text_height + 10

    paci_font_path = download_file_from_gcs("pacifico.ttf")
    font = ImageFont.truetype(paci_font_path, 30)
    text_width, text_height = draw.textbbox((0, 0), author, font=font)[2:]
    x = (image_width - text_width) / 2
    draw.text((x, y + 10), author, font=font, fill="black")
    background.save(f"/tmp/{output_filename}")


def get_full_path(filename: str):
    return os.path.join(BASE_DIR, filename)


# Function to post the image to Twitter
def post_image_to_twitter(image_name, message):
    image_path = f"/tmp/{image_name}"
    media: tweepy.models.Media = api.media_upload(filename=image_path)
    newapi.create_tweet(text=message, media_ids=[media.media_id])
    # tweet = api.update_status(status=message, media_ids=[media.media_id])


def main() -> tuple:
    quote = get_quote()
    if quote:
        rating, improvement, caption = rate_and_improve_quote(quote)

        if rating and int(rating) > 6.5:

            create_image_with_quote(improvement)
            post_image_to_twitter(output_filename, caption)
            return (True, improvement, caption)
        else:
            return (False, improvement, caption)
    return (False, "", "")


@https_fn.on_request()
def quote_echoes(req: https_fn.Request) -> https_fn.Response:

    is_success, quote, caption = main()
    if is_success:
        message = "Successful"
    else:
        message = "Failed"
    return https_fn.Response(message)
