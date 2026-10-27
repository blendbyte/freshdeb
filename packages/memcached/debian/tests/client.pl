#!/usr/bin/perl -w

use strict;
use Test::More;
use IO::Socket::INET;

my $host = '127.0.0.1';
my $port = '11211';
my $test_key = "mykey";
my $test_value = "mycustomvalue";
my $test_value_length = length($test_value);
my $data;

sub new_conn {
	my $conn = IO::Socket::INET->new(PeerAddr => "$host:$port",
									 Proto => 'tcp'
				) or die "Fail to connect to memcached on $host:$port: $!";
	return $conn;
}

plan tests => 6;

#####
# Test that UDP is not available by default

my $netstat = `ss --listening --udp --process --numeric 'sport = :$port' | grep --invert-match '^State'`;
ok($netstat !~ /11211/, "memcached is not listening on UDP");

#################
# Stats

my $sock = new_conn();
print $sock "stats\r\n";
$sock->recv($data, 1024);
ok($data =~ /uptime/, "STAT output");
$sock->close();


#################
# Set a key/value pair

$sock = new_conn();
print $sock "set $test_key 0 60 $test_value_length\r\n$test_value\r\n";
$sock->recv($data, 1024);
ok($data =~ /STORED/, "Stored $test_value at key $test_value");
$sock->close();


#################
# Get the value of the key

$sock = new_conn();
print $sock "gets $test_key\r\n";
my $line = scalar(<$sock>);
if ($line =~ /^VALUE/) {
	my $value = scalar(<$sock>) . scalar(<$sock>);
	if ($value !~ /$test_value/) {
		fail("Error getting $test_key value $value");
	}

	# remove the END of body
	$value =~ s/\r\nEND\r\n//;

	pass("$test_key value found");
}
else {
	fail("Error $test_key is missing: $line");
}


#################
# Flush all keys

$sock = new_conn();
print $sock "flush_all\r\n";
$sock->recv($data, 1024);
ok($data =~ /^OK/, "flush_all");


#################
# Verify that our key is missing

$sock = new_conn();
print $sock "gets $test_key\r\n";

$sock->recv($data, 1024);
if ($data !~ /^END/) {
	$data =~ s/\n/ /g;
	$data =~ s/\r/ /g;
	fail("$test_key found after flush");
}
else {
	pass("$test_key is not found after flush");
}

done_testing;
