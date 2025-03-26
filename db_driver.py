import pyodbc
from typing import Optional
from dataclasses import dataclass
from contextlib import contextmanager
import logging


logger = logging.getLogger("user-data")
logger.setLevel(logging.INFO)


@dataclass
class Contact:
    phone: str
    mail: str
    name: str
    companyName: str
    meetingTs: str


class DatabaseDriver:
    def __init__(self, server: str, database: str, username: str, password: str):
        self.conn_str = (
            f"DRIVER={{SQL Server}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password}"
        )
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = None
        try:
            conn = pyodbc.connect(self.conn_str)
            logger.info("Database connection established successfully")
            yield conn
        except Exception as e:
            logger.error(f"Database connection error: {str(e)}")
            raise
        finally:
            if conn:
                conn.close()
                logger.info("Database connection closed")

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'contacts')
                    BEGIN
                        CREATE TABLE contacts (
                            name NVARCHAR(50) NOT NULL,
                            phone NVARCHAR(50) PRIMARY KEY,
                            mail NVARCHAR(50) NOT NULL,
                            companyName NVARCHAR(50) NOT NULL,
                            meetingTs NVARCHAR(50) NOT NULL
                        )
                    END
                """)
                conn.commit()
                logger.info("Database initialized successfully")
            except Exception as e:
                logger.error(f"Database initialization error: {str(e)}")
                raise

    def create_contact(self, phone: str, mail: str, companyName: str, name: str, meetingTs: str) -> Optional[Contact]:
        try:
            logger.info(f"Attempting to create contact: phone={phone}, name={name}")
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM contacts WHERE phone = ?", (phone,))
                if cursor.fetchone()[0] > 0:
                    logger.warning(f"Contact with phone {phone} already exists")
                    return None

                cursor.execute(
                    """
                    INSERT INTO contacts (name, phone, mail, companyName, meetingTs)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, phone, mail, companyName, meetingTs)
                )
                conn.commit()
                logger.info(f"Contact created successfully: phone={phone}")

                return Contact(phone=phone, mail=mail, name=name, companyName=companyName, meetingTs=meetingTs)
        except Exception as e:
            logger.error(f"Error creating contact: {str(e)}")
            return None

    def get_contact_by_phone(self, phone: str) -> Optional[Contact]:
        try:
            logger.info(f"Attempting to fetch contact with phone: {phone}")
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name, phone, mail, companyName, meetingTs FROM contacts WHERE phone = ?", (phone,))
                row = cursor.fetchone()
                if not row:
                    logger.info(f"No contact found with phone: {phone}")
                    return None

                contact = Contact(name=row[0], phone=row[1], mail=row[2], companyName=row[3], meetingTs=row[4])
                logger.info(f"Contact found: {contact}")
                return contact
        except Exception as e:
            logger.error(f"Error fetching contact: {str(e)}")
            return None

    def update_contact(self, phone: str, **fields_to_update) -> Optional[Contact]:
        try:
            logger.info(f"Attempting to update contact with phone: {phone}")

            existing_contact = self.get_contact_by_phone(phone)
            if not existing_contact:
                logger.warning(f"Contact with phone {phone} not found")
                return None

            valid_fields = {'name', 'mail', 'companyName', 'meetingTs'}
            update_fields = {k: v for k, v in fields_to_update.items() if k in valid_fields}

            if not update_fields:
                logger.warning("No valid fields to update")
                return existing_contact

            set_clause = ", ".join([f"{field} = ?" for field in update_fields.keys()])
            query = f"UPDATE contacts SET {set_clause} WHERE phone = ?"
            params = tuple(update_fields.values()) + (phone,)

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()

            updated_contact = self.get_contact_by_phone(phone)
            logger.info(f"Contact updated successfully: {updated_contact}")
            return updated_contact

        except Exception as e:
            logger.error(f"Error updating contact: {str(e)}")
            return None
