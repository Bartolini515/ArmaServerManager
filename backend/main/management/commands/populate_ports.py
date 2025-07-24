from django.core.management.base import BaseCommand
from main.models import Ports

class Command(BaseCommand):
    help = 'Populates the database with a range of ports.'

    def add_arguments(self, parser):
        parser.add_argument('start_port', type=int, help='The starting port number.')
        parser.add_argument('end_port', type=int, help='The ending port number.')

    def handle(self, *args, **options):
        start_port = options['start_port']
        end_port = options['end_port']

        for port_num in range(start_port, end_port + 1):
            Ports.objects.get_or_create(port_number=port_num)
        
        self.stdout.write(self.style.SUCCESS(f'Successfully populated ports from {start_port} to {end_port}'))