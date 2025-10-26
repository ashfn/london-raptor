# data module that can be imported to interact with the DB
from peewee import *

db = SqliteDatabase('london.db')

class BaseModel(Model):
    class Meta:
        database = db

class Point(BaseModel):
    point_id = CharField(primary_key=True)
    latitude = FloatField()
    longitude = FloatField()
    name = CharField()
    mode = CharField()

class Connection(BaseModel):
    origin_point_id = CharField()
    destination_point_id = CharField()
    line_id = CharField()
    direction = CharField()
    class Meta:
        primary_key = CompositeKey('origin_point_id', 'destination_point_id', 'line_id', 'direction')

def connect_db():
    try:
        db.connect()
        db.create_tables([Point, Connection])
        return db
    except OperationalError as e:
        print(f"Error connecting to database: {e}")
        return None

