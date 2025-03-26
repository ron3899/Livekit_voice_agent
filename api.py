from datetime import datetime, timedelta
import enum
import logging
import re
from typing import Annotated, Dict, Any, Optional
from dataclasses import dataclass
from contextlib import contextmanager
import pyodbc
from livekit.agents import llm
import requests
from db_driver import DatabaseDriver,Contact
from config import DB_CONFIG

# Logger setup
logger = logging.getLogger("user-data")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)

DB = DatabaseDriver(**DB_CONFIG)

class ContactDetails(enum.Enum):
    """Enum for contact details fields"""
    Name = "name"
    Phone = "phone"
    Mail = "mail"
    CompanyName = "companyName"
    MeetingTs = "meetingTs"

class CalendarManager:
    def __init__(self):
        self.grant_id = '78ea692d-06d0-425b-bedb-4db1f3fcd143'
        self.calendar_id = 'AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg0AKcd46Hz_F0SmwneJwcS8OQAAAAGRYgAA'
        self.api_token = 'nyk_v0_PmSyBJvY41BEaWN96XofJGC29QeULRdrSjg1XRdKLUFLP9faGicVwPhsOOQ4Z0Vq'
        self.headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        }

    async def check_availability(self, meeting_time: datetime) -> bool:
        url = f'https://api.us.nylas.com/v3/grants/{self.grant_id}/events'
        start_time = int((meeting_time + timedelta(minutes=30)).timestamp())
        end_time = int((meeting_time + timedelta(minutes=90)).timestamp())

        params = {
            'calendar_id': self.calendar_id,
            'limit': 3
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            events = response.json()

            if 'data' not in events:
                logger.error("Invalid response format from calendar API")
                return False

            for event in events['data']:
                if 'when' in event:
                    event_start = int(event['when']['start_time'])
                    event_end = int(event['when']['end_time'])
                    if not (end_time <= event_start or start_time >= event_end):
                        return False

            return True

        except Exception as e:
            logger.error(f"Error checking calendar availability: {str(e)}")
            return False

    async def schedule_meeting(self, contact:Contact, meeting_time: datetime) -> bool:
        url = f'https://api.us.nylas.com/v3/grants/{self.grant_id}/events'

        start_time = int((meeting_time + timedelta(minutes=30)).timestamp())
        end_time = int((meeting_time + timedelta(minutes=90)).timestamp())

        event_data = {
            'calendar_id': self.calendar_id,
            'title': f'פגישה עם {contact.name}',
            'description': f'פגישה שנקבעה אוטומטית עם {contact.name} מחברת {contact.companyName},מספר הטלפון{contact.phone}',
            'when': {
                'start_time': start_time,
                'end_time': end_time,
                'start_timezone': 'Asia/Jerusalem',
                'end_timezone': 'Asia/Jerusalem'
            },
            'location': 'המשרד',
            'participants': [
                {'email': contact.mail, 'name': 
                 contact.name}
            ],
            'busy': True,
            'visibility': 'public'
        }

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json=event_data,
                params={'calendar_id': self.calendar_id}
            )
            response.raise_for_status()
            logger.info(f"Meeting scheduled successfully for {contact.name}")
            return True

        except Exception as e:
            logger.error(f"Error scheduling meeting: {str(e)}")
            return False

class AssistantFnc(llm.FunctionContext):
    def __init__(self):
        super().__init__()
        self.db = DatabaseDriver(**DB_CONFIG)
        self.calendar = CalendarManager()
        self._current_contact = None

    def _parse_meeting_time(self, meeting_time_str: str) -> Optional[datetime]:
        try:
            if not meeting_time_str:
                return None
            return datetime.fromisoformat(meeting_time_str)
        except ValueError:
            logger.error(f"Invalid meeting time format: {meeting_time_str}")
            return None

    @llm.ai_callable(description="lookup a contact by phone number")
    def lookup_contact(self, phone: Annotated[str, llm.TypeInfo(description="Contact's phone number")]) -> str:
        contact = self.db.get_contact_by_phone(phone)
        if not contact:
            return "Contact not found"

        self._current_contact = contact
        return f"Contact found:\nName: {contact.name}\nPhone: {contact.phone}\nEmail: {contact.mail}\nCompany: {contact.companyName}\nMeeting Time: {contact.meetingTs}"

    @llm.ai_callable(description="create new contact")
    async def create_contact(
            self,
            phone: Annotated[str, llm.TypeInfo(description="Contact's phone number")],
            mail: Annotated[str, llm.TypeInfo(description="Contact's email")],
            name: Annotated[str, llm.TypeInfo(description="Contact's name")],
            companyName: Annotated[str, llm.TypeInfo(description="Contact's company name")],
            meetingTs: Annotated[str, llm.TypeInfo(description="Preferred meeting time")]
    ) -> str:
        try:
            contact = self.db.create_contact(phone, mail, companyName, name, meetingTs)
            if not contact:
                return "Failed to create contact"

            self._current_contact = contact
            return "Contact created successfully"
        except Exception as e:
            logger.error(f"Error in create_contact: {str(e)}")
            return f"Failed to create contact: {str(e)}"

    @llm.ai_callable(description="Schedule meeting for existing contact")
    async def schedule_meeting_for_contact(self) -> str:
        try:
            if not self._current_contact:
                return "No contact selected. Please lookup or create a contact first."

            meeting_time = self._parse_meeting_time(self._current_contact.meetingTs)
            if not meeting_time:
                return "Invalid meeting time format"

            is_available = await self.calendar.check_availability(meeting_time)
            if not is_available:
                return "Selected time slot is not available"

            success = await self.calendar.schedule_meeting(self._current_contact, meeting_time)
            if not success:
                return "Failed to schedule meeting"

            return f"Meeting scheduled successfully for {self._current_contact.name}"
        except Exception as e:
            logger.error(f"Error in schedule_meeting_for_contact: {str(e)}")
            return f"Failed to schedule meeting: {str(e)}"

    @llm.ai_callable(description="Create contact and schedule meeting")
    async def create_contact_with_meeting(
            self,
            phone: Annotated[str, llm.TypeInfo(description="Contact's phone number")],
            mail: Annotated[str, llm.TypeInfo(description="Contact's email")],
            name: Annotated[str, llm.TypeInfo(description="Contact's name")],
            companyName: Annotated[str, llm.TypeInfo(description="Contact's company name")],
            meetingTs: Annotated[str, llm.TypeInfo(description="Preferred meeting time")]
    ) -> str:
        try:
            contact_result = await self.create_contact(phone, mail, name, companyName, meetingTs)
            if "Failed" in contact_result:
                return contact_result

            meeting_result = await self.schedule_meeting_for_contact()
            return f"{contact_result} and {meeting_result}"
        except Exception as e:
            logger.error(f"Error in create_contact_with_meeting: {str(e)}")
            return f"Error creating contact with meeting: {str(e)}"
        

    @llm.ai_callable(description="Update contact details")
    async def update_contact_details(
        self,
        phone: Annotated[str, llm.TypeInfo(description="Contact's phone number")],
        mail: Annotated[Optional[str], llm.TypeInfo(description="New email address")] = None,
        name: Annotated[Optional[str], llm.TypeInfo(description="New name")] = None,
        companyName: Annotated[Optional[str], llm.TypeInfo(description="New company name")] = None,
        meetingTs: Annotated[Optional[str], llm.TypeInfo(description="New meeting time")] = None
    ) -> str:
    
        try:
            update_fields = {
                k: v for k, v in {
                    'mail': mail,
                    'name': name,
                    'companyName': companyName,
                    'meetingTs': meetingTs
                }.items() if v is not None
            }

            if not update_fields:
                return "No fields provided for update"

            updated_contact = self.db.update_contact(phone, **update_fields)

            if not updated_contact:
                return f"Failed to update contact with phone: {phone}"

            return f"Contact with phone {phone} updated successfully"
        
        except Exception as e:
            logger.error(f"Error in update_contact_details: {str(e)}")
            return f"Failed to update contact: {str(e)}"

