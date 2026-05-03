# Comprehensive Redmine Seeding Script
# This script initializes Redmine with Trackers, Priorities, Statuses, Projects, Users, and Issues.

puts "Starting Redmine seeding..."

# 1. Enable REST API
Setting.rest_api_enabled = '1'
puts "REST API enabled."

# 2. Issue Statuses
statuses = [
  { name: 'New', is_closed: false },
  { name: 'In Progress', is_closed: false },
  { name: 'Resolved', is_closed: false },
  { name: 'Feedback', is_closed: false },
  { name: 'Closed', is_closed: true },
  { name: 'Rejected', is_closed: true }
]
statuses.each do |s|
  IssueStatus.find_or_create_by!(name: s[:name]) do |status|
    status.is_closed = s[:is_closed]
  end
end
puts "Issue statuses created."

# 3. Issue Priorities
priorities = ['Low', 'Normal', 'High', 'Urgent', 'Immediate']
priorities.each_with_index do |name, index|
  IssuePriority.find_or_create_by!(name: name) do |p|
    p.position = index + 1
    p.is_default = (name == 'Normal')
  end
end
puts "Issue priorities created."

# 4. Trackers
trackers = ['Bug', 'Feature', 'Support']
trackers.each do |name|
  Tracker.find_or_create_by!(name: name) do |t|
    t.default_status = IssueStatus.find_by(name: 'New')
  end
end
puts "Trackers created."

# 5. Roles
manager_role = Role.find_or_create_by!(name: 'Manager') do |r|
  r.permissions = Role.find_by(name: 'Manager')&.permissions || [:view_issues, :add_issues, :edit_issues, :manage_members]
end
developer_role = Role.find_or_create_by!(name: 'Developer') do |r|
  r.permissions = [:view_issues, :add_issues, :edit_issues]
end
puts "Roles created."

# 6. Custom Fields for Projects
cf_city = ProjectCustomField.find_or_create_by!(name: 'City', field_format: 'string')
cf_location = ProjectCustomField.find_or_create_by!(name: 'Customer Office Location', field_format: 'string')
cf_type = ProjectCustomField.find_or_create_by!(name: 'Project Type', field_format: 'string')
puts "Custom fields created."

# 7. Sample Project: AXIS
project = Project.find_or_create_by!(identifier: 'axis') do |p|
  p.name = 'AXIS'
  p.description = 'Main project for AXIS customer'
  p.trackers = Tracker.all
end
project.custom_field_values = {
  cf_city.id.to_s => 'Delhi',
  cf_location.id.to_s => 'Gurgaon Office',
  cf_type.id.to_s => 'Onsite'
}
project.save!
puts "Project AXIS created."

# 8. Sample User: test@gmail.com
begin
  user = User.find_by_mail('test@gmail.com')
  if user.nil?
    user = User.new(mail: 'test@gmail.com')
    user.login = 'test_user'
    user.firstname = 'Test'
    user.lastname = 'User'
    user.password = 'Password123!'
    user.admin = false
    unless user.save
      puts "Failed to create user: #{user.errors.full_messages.join(', ')}"
      exit 1
    end
  end
  
  member = Member.find_or_initialize_by(project: project, user: user)
  if member.new_record?
    member.roles << manager_role
    member.save!
  end
rescue => e
  puts "Error during user creation: #{e.message}"
  exit 1
end
puts "User test@gmail.com created and assigned to AXIS."

# 9. Sample Issues
begin
  issue1 = Issue.find_or_initialize_by(subject: 'Initial API Setup', project: project)
  if issue1.new_record?
    issue1.author = user
    issue1.assigned_to = user
    issue1.tracker = Tracker.find_by(name: 'Feature')
    issue1.status = IssueStatus.find_by(name: 'New')
    issue1.priority = IssuePriority.find_by(name: 'Normal')
    issue1.description = 'Set up the basic API endpoints for Redmine integration.'
    issue1.save!
  end

  issue2 = Issue.find_or_initialize_by(subject: 'Fix Authentication Bug', project: project)
  if issue2.new_record?
    issue2.author = user
    issue2.assigned_to = user
    issue2.tracker = Tracker.find_by(name: 'Bug')
    issue2.status = IssueStatus.find_by(name: 'In Progress')
    issue2.priority = IssuePriority.find_by(name: 'High')
    issue2.description = 'Authentication token is expiring too quickly.'
    issue2.save!
  end
rescue => e
  puts "Error during issue creation: #{e.message}"
  exit 1
end
puts "Sample issues created."

puts "Seeding complete!"
