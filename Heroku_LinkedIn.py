import os
import time
import re
import traceback
import openai
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Set up Chrome options for headless browsing
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--no-sandbox")
chrome_options.binary_location = os.environ.get("GOOGLE_CHROME_BIN")

# Set up the web driver
driver = webdriver.Chrome(executable_path=os.environ.get("CHROMEDRIVER_PATH"), options=chrome_options)

# Get LinkedIn credentials from environment variables
linkedin_email = os.environ.get('LINKEDIN_EMAIL')
linkedin_password = os.environ.get('LINKEDIN_PASSWORD')

if not linkedin_email or not linkedin_password:
    raise ValueError("LinkedIn credentials not found in environment variables.")

# Set up OpenAI API
openai.api_key = os.environ.get('OPENAI_API_KEY')

if not openai.api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

# Set up Google Sheets API
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

# Use service account info from environment variable
service_account_info = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
if not service_account_info:
    raise ValueError("Google service account credentials not found in environment variables.")

creds = service_account.Credentials.from_service_account_info(
    eval(service_account_info), scopes=SCOPES)
sheets_service = build('sheets', 'v4', credentials=creds)

def get_urls_and_statuses_from_sheet():
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range='Sheet1!H:I'  # Link column is H, Status column is I
    ).execute()
    values = result.get('values', [])
    
    if not values:
        print('No data found in the sheet.')
        return []
    
    # Skip header and filter out empty cells
    return [(row[0], row[1] if len(row) > 1 else "") for row in values[1:] if row]

def update_status(row_index, status):
    range_name = f'Sheet1!I{row_index}'
    body = {
        'values': [[status]]
    }
    result = sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption='USER_ENTERED', body=body).execute()
    print(f"Status updated for row {row_index}: {status}")

def save_to_google_sheets(data):
    range_name = 'Sheet3!A1:G1'  # Start from A1 in Sheet3

    # Check if header exists
    sheet = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=range_name).execute()
    header_exists = len(sheet.get('values', [])) > 0

    # Define the headers
    headers = ["Name", "Job Title", "Profile Link", "Info", "More info", "OpenAI", "Original URL"]

    # Prepare the data to be appended
    values = []
    if not header_exists:
        values.append(headers)
    values.append(list(data.values()))

    body = {'values': values}

    result = sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption='USER_ENTERED', insertDataOption='INSERT_ROWS', body=body).execute()

    print(f"{result.get('updates').get('updatedCells')} cells appended.")

def clean_text(text):
    text = re.sub(r'#\S+\s*', '', text)
    text = re.sub(r'\bhashtag\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def wait_and_get_element(xpath, wait_time=20):
    try:
        element = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        print(f"Element found: {xpath}")
        return element
    except Exception as e:
        print(f"Error finding element {xpath}: {e}")
        return None

def wait_and_get_elements(xpath, wait_time=20):
    try:
        elements = WebDriverWait(driver, wait_time).until(
            EC.presence_of_all_elements_located((By.XPATH, xpath))
        )
        print(f"Elements found: {xpath}, count: {len(elements)}")
        return elements
    except Exception as e:
        print(f"Error finding elements {xpath}: {e}")
        return []

def login():
    try:
        print("Attempting to log in")
        driver.get('https://www.linkedin.com/login')
        email_field = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, 'username')))
        email_field.clear()
        email_field.send_keys(linkedin_email)

        password_field = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, 'password')))
        password_field.clear()
        password_field.send_keys(linkedin_password)

        login_button = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.btn__primary--large')))
        login_button.click()
        print("Login button clicked")

        # Wait for successful login
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, 'global-nav')))
        print("Successfully logged in")

    except Exception as e:
        print(f"Error during login: {e}")
        print(traceback.format_exc())
        raise

def click_view_job_button():
    try:
        xpath = '//*[@id="fie-impression-container"]/div[3]/div[1]/button[1]/span[2]'
        
        view_job_button = wait_and_get_element(xpath)
        if view_job_button:
            view_job_button.click()
            print("'View Job' button clicked")
            return True
        else:
            print("'View Job' button not found")
            return False
    except ElementClickInterceptedException:
        print("Click was intercepted, trying again...")
        return False
    except Exception as e:
        print(f"Error clicking 'View Job' button: {e}")
        return False

def format_openai_response(response):
    try:
        response = re.sub(r'[{}"]+', '', response)
        response = re.sub(r'\s*:\s*', ': ', response)
        response = re.sub(r',\s*', '\n', response)
        formatted_response = response.strip()
        return formatted_response
    except Exception as e:
        print(f"Error formatting OpenAI response: {e}")
        return response

def process_with_openai(info, more_info):
    prompt = f"""
    Given the following data from a LinkedIn job post, extract and format the following information:
    - Person to contact
    - Email (if available)
    - Phone number (if available)
    - Job title
    - Job location
    - Company name
    - Key job requirements
    - Any other relevant details for a job application

    Data:
    Info: {info}
    More Info: {more_info}

    Please format the output as a structured JSON object.
    """

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that extracts and structures job posting information. Process the data and give output in a clean and formatted manner (free of quotes and brackets) so its easy to read and look clean"},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message['content']

def main():
    try:
        print("Starting LinkedIn scraper script")
        login()
        time.sleep(10)  # Increased wait time after login

        urls_and_statuses = get_urls_and_statuses_from_sheet()
        
        if not urls_and_statuses:
            print("No URLs found in the spreadsheet. Exiting.")
            return

        print(f"Found {len(urls_and_statuses)} URLs to process")

        for row_index, (url, status) in enumerate(urls_and_statuses, start=2):  # start=2 because row 1 is header
            if status.lower() == "done":
                print(f"Skipping already processed URL: {url}")
                continue

            if not url:
                print(f"No link found in row {row_index}")
                update_status(row_index, "Link not found")
                continue

            try:
                print(f"Processing URL: {url}")
                driver.get(url)
                print(f"Navigated to URL: {url}")
                time.sleep(15)  # Increased wait time after navigation

                # Scrape post information
                name_element = wait_and_get_element("//span[contains(@class, 'update-components-actor__name')]")
                name = name_element.text.strip() if name_element else "Name not found"

                job_title_element = wait_and_get_element("//span[contains(@class, 'update-components-actor__description')]")
                job_title = job_title_element.text.strip() if job_title_element else "Job title not found"

                # Remove duplicates from name and job title
                name = ' '.join(dict.fromkeys(name.split()))
                job_title = ' '.join(dict.fromkeys(job_title.split()))

                profile_link_element = wait_and_get_element("//a[contains(@class, 'update-components-actor__meta-link')]")
                profile_link = profile_link_element.get_attribute('href') if profile_link_element else "Profile link not found"

                info_element = wait_and_get_element("//div[contains(@class, 'update-components-text')]")
                info = clean_text(info_element.text) if info_element else "Info not found"

                # Try to find and click "View job" button
                if click_view_job_button():
                    # Scrape job details with the new XPath
                    job_details_element = wait_and_get_element("//div[contains(@class, 'job-details')]/child::h1")
                    job_details = job_details_element.text if job_details_element else "Job details not found"

                    additional_info_elements = wait_and_get_elements("//div[contains(@class, 'primary-description')]/div/span[@class]")
                    additional_info = [element.text for element in additional_info_elements]

                    skills_required_element = wait_and_get_element("//div[@id='how-you-match-card-container']/section[2]/div/div/div/div/a")
                    skills_required = skills_required_element.text if skills_required_element else "Skills required not found"

                    more_info = f"{job_details}, " + ", ".join(additional_info) + f", {skills_required}"
                else:
                    more_info = "Failed to click 'View job' button"

                print(f'More Info: {more_info}')

                # Process the data through OpenAI
                scraped_data = {
                    'name': name,
                    'job_title': job_title,
                    'profile_link': profile_link,
                    'info': info,
                    'more_info': more_info,
                    'openai': format_openai_response(process_with_openai(info, more_info)),
                    'original_url': url
                }

                print("Data processed through OpenAI")

                # Save the processed data to Google Sheets
                save_to_google_sheets(scraped_data)
                print("Data saved to Google Sheets successfully.")

                # Update status to "Done"
                update_status(row_index, "Done")

            except Exception as e:
                print(f"An error occurred while processing URL {url}: {e}")
                print(traceback.format_exc())
                update_status(row_index, "Error")

    except Exception as e:
        print(f"An error occurred: {e}")
        print(traceback.format_exc())
    finally:
        driver.quit()
        print("Web driver closed.")

if __name__ == "__main__":
    main()